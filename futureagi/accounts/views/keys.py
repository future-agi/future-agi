import traceback
from math import ceil

import structlog
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

from accounts.key_cache import publish_key_revocation as _publish_key_revocation
from accounts.models import OrgApiKey
from accounts.permissions import RequiresIndependentAuth
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

logger = structlog.get_logger(__name__)


def _managed_keys(organization):
    """The keys a member manages from any workspace: those the workspace sees, and
    every coding agent key of the organization, which is bound to its environment's
    workspace but must stay revocable from anywhere."""
    return OrgApiKey.objects.filter(
        organization=organization
    ) | OrgApiKey.no_workspace_objects.filter(
        organization=organization, type="coding_agent"
    )


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

    def get_permissions(self):
        permission_classes = list(self.permission_classes)
        if self.action == "generate_secret_key":
            permission_classes.append(RequiresIndependentAuth)
        return [permission() for permission in permission_classes]

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
        "expiresAt": "expires_at",
        "expires_at": "expires_at",
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
            # Sandbox ingest keys belong to the platform, not to the user.
            apiKeys = (
                _managed_keys(user_organization)
                .filter(deleted=False)
                .exclude(type="ingest")
                .select_related("user", "minted_by")
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
                    "created_by": getattr(key.user or key.minted_by, "name", None),
                    "created_at": key.created_at,
                    "enabled": key.enabled,
                    "type": key.type,
                    "expires_at": key.expires_at,
                    "is_expired": key.is_expired,
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
                apikey = _managed_keys(
                    getattr(request, "organization", None) or request.user.organization
                ).get(id=key_id)
                if apikey.enabled:
                    return self._gm.bad_request(get_error_message("API_KEY_ENABLED"))
                if apikey.is_expired:
                    # Auth would disable it again on its next use.
                    return self._gm.bad_request(
                        get_error_message("API_KEY_EXPIRED_CANNOT_ENABLE")
                    )
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
                apikey = _managed_keys(
                    getattr(request, "organization", None) or request.user.organization
                ).get(id=key_id)
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

            key_type = request.validated_data["type"]
            environment_id = request.validated_data.get("simulation_environment_id")
            workspace = None
            if key_type == "coding_agent":
                # Imported here: accounts cannot depend on simulate at load.
                from simulate.authentication import bindable_environment

                environment = bindable_environment(request, environment_id)
                if environment is None:
                    return self._gm.not_found("Environment not found")
                # The key's workspace outranks any header, so its calls always
                # see the environment's workspace.
                workspace = environment.workspace

            # A coding agent key acts as its minter but names no user, so builds
            # that predate it refuse it instead of acting as that person.
            owner = (
                {"minted_by": request.user}
                if key_type == "coding_agent"
                else {"user": request.user}
            )
            org_key = OrgApiKey.objects.create(
                name=key_name,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                type=key_type,
                workspace=workspace,
                simulation_environment_id=environment_id,
                **owner,
                expires_at=request.validated_data.get("expires_at"),
            )
            try:
                from tfc.deployment_telemetry.events import record_event

                record_event(
                    "api_key_created",
                    actor_type="api_key",
                    actor_id=org_key.id,
                    source="web",
                    organization_id=org_key.organization_id,
                )
            except Exception:
                logger.debug("deployment_telemetry_api_key_event_failed", exc_info=True)
            response = {
                "key_id": org_key.id,
                "key_name": org_key.name,
                "api_key": org_key.api_key,
                "masked_api_key": mask_key(org_key.api_key),
                "secret_key": org_key.secret_key,
                "masked_secret_key": mask_key(org_key.secret_key),
                "expires_at": org_key.expires_at,
            }
            return self._gm.success_response(response)
        except APIException:
            # A refusal with its own status and code, such as an environment a coding
            # agent key cannot be bound to.
            raise
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
            org_key = _managed_keys(
                getattr(request, "organization", None) or request.user.organization
            ).get(id=key_id)
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
