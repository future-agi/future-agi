import math
from datetime import UTC, datetime

import structlog
from django.db import IntegrityError
from django.http import Http404
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from accounts.utils import get_request_organization
from integrations.models import (
    ACTION_ONLY_PLATFORMS,
    ConnectionStatus,
    IntegrationConnection,
)
from integrations.serializers.contracts import (
    INTEGRATION_ERROR_RESPONSES,
    INTEGRATION_SYNC_ERROR_RESPONSES,
    IntegrationConnectionDetailResponseSerializer,
    IntegrationConnectionListQuerySerializer,
    IntegrationConnectionListResponseSerializer,
    IntegrationEmptyRequestSerializer,
    IntegrationMessageResponseSerializer,
    IntegrationValidationResponseSerializer,
)
from integrations.serializers.integration_connection import (
    IntegrationConnectionCreateSerializer,
    IntegrationConnectionDetailSerializer,
    IntegrationConnectionListSerializer,
    IntegrationConnectionUpdateSerializer,
    ValidateCredentialsSerializer,
)
from integrations.services.base import get_integration_service
from integrations.services.credentials import CredentialManager
from integrations.services.registry import ensure_services_loaded
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tracer.models.project import Project, ProjectSourceChoices
from tracer.utils.otel import get_or_create_project

logger = structlog.get_logger(__name__)


integration_errors = swagger_auto_schema(responses=INTEGRATION_ERROR_RESPONSES)


def _build_credentials(data: dict) -> dict:
    """Build a credentials dict from serializer data.

    If a `credentials` JSON dict is provided (new platforms), use it.
    Otherwise fall back to public_key/secret_key (Langfuse compat).
    """
    creds = data.get("credentials") or {}
    if creds:
        return creds
    pk = data.get("public_key", "")
    sk = data.get("secret_key", "")
    if pk or sk:
        return {"public_key": pk, "secret_key": sk}
    return {}


def _success_response(result, status_code=status.HTTP_200_OK):
    return Response({"status": True, "result": result}, status=status_code)


def _error_response(message, status_code=status.HTTP_400_BAD_REQUEST):
    return Response(
        build_error_envelope(message, status_code=status_code),
        status=status_code,
    )


def _validation_error_response(errors):
    return _error_response(errors, status.HTTP_400_BAD_REQUEST)


@method_decorator(name="retrieve", decorator=integration_errors)
@method_decorator(name="destroy", decorator=integration_errors)
class IntegrationConnectionViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """API endpoints for managing integration connections."""

    serializer_class = IntegrationConnectionListSerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == "list":
            return IntegrationConnectionListSerializer
        if self.action == "retrieve":
            return IntegrationConnectionDetailSerializer
        return IntegrationConnectionListSerializer

    # ─── LIST ────────────────────────────────────────────────────

    @validated_request(
        query_serializer=IntegrationConnectionListQuerySerializer,
        responses={
            200: IntegrationConnectionListResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            total_count = queryset.count()

            page_number = request.validated_query_data.get("page_number", 0)
            page_size = request.validated_query_data.get("page_size", 20)

            start = page_number * page_size
            end = start + page_size

            total_pages = math.ceil(total_count / page_size) if page_size > 0 else 0
            next_page_number = (
                page_number + 1 if (page_number + 1) < total_pages else None
            )

            paginated_queryset = queryset[start:end]
            serializer = IntegrationConnectionListSerializer(
                paginated_queryset, many=True
            )

            return _success_response(
                {
                    "metadata": {
                        "total_count": total_count,
                        "current_page": page_number,
                        "page_size": page_size,
                        "total_pages": total_pages,
                        "next_page": next_page_number,
                    },
                    "connections": serializer.data,
                }
            )
        except Exception as e:
            logger.exception("Error listing integration connections", error=str(e))
            return _error_response(
                "Failed to list integration connections.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── RETRIEVE ────────────────────────────────────────────────

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = IntegrationConnectionDetailSerializer(instance)
            data = serializer.data
            logger.info(
                "Retrieve connection detail",
                connection_id=str(instance.id),
                display_name=data.get("display_name"),
                host_url=data.get("host_url"),
                external_project_name=data.get("external_project_name"),
                public_key_display=data.get("public_key_display"),
                project_name=data.get("project_name"),
            )
            return _success_response(data)
        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception("Error retrieving integration connection", error=str(e))
            return _error_response(
                "Failed to retrieve connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── CREATE ──────────────────────────────────────────────────

    @validated_request(
        request_serializer=IntegrationConnectionCreateSerializer,
        responses={
            201: IntegrationConnectionDetailResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    def create(self, request, *args, **kwargs):
        try:
            data = request.validated_data

            # Ensure workspace is available
            workspace = getattr(request, "workspace", None)
            if not workspace:
                return _error_response(
                    "Workspace context is required to create an integration."
                )

            ensure_services_loaded()

            # 1. Validate credentials against the platform
            service = get_integration_service(data["platform"])
            credentials = _build_credentials(data)
            ca_cert = data.get("ca_certificate") or None
            host_url = data.get("host_url") or ""

            validation = service.validate_credentials(
                host_url=host_url,
                credentials=credentials,
                ca_certificate=ca_cert,
            )

            if not validation.get("valid"):
                return _error_response(
                    validation.get("error", "Invalid credentials.")
                )

            # Resolve external project name from request or validation
            ext_project_name = data.get("external_project_name") or ""
            if not ext_project_name:
                # Fallback: first project from validation, or platform name
                projects = validation.get("projects", [])
                ext_project_name = projects[0]["name"] if projects else data["platform"]

            # 2. Encrypt credentials
            encrypted = CredentialManager.encrypt(credentials)

            # 3. Resolve FutureAGI project
            project = None
            project_id = data.get("project_id")
            if project_id:
                try:
                    project = Project.objects.get(
                        id=project_id,
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                    )
                except Project.DoesNotExist:
                    return _error_response("Selected project not found.")
            else:
                project_name = data.get("new_project_name") or ext_project_name
                _org = get_request_organization(request)
                project = get_or_create_project(
                    project_name=project_name,
                    organization_id=str(_org.id) if _org else None,
                    project_type="observe",
                    user_id=str(request.user.id),
                    workspace_id=str(workspace.id),
                    source=(
                        ProjectSourceChoices.INTEGRATION.value
                        if hasattr(ProjectSourceChoices, "INTEGRATION")
                        else ProjectSourceChoices.PROTOTYPE.value
                    ),
                )

            # 4. Determine initial status based on backfill option
            backfill_option = data.get("backfill_option", "all")
            initial_status = ConnectionStatus.ACTIVE
            backfill_from = None
            backfill_completed = True

            if backfill_option == "all":
                initial_status = ConnectionStatus.BACKFILLING
                backfill_completed = False
            elif backfill_option == "from_date":
                initial_status = ConnectionStatus.BACKFILLING
                backfill_from = data.get("backfill_from_date")
                backfill_completed = False

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Action-only platforms (Linear, etc.) use org-wide credentials
            # with no per-project mapping. The partial unique constraint
            # restricts them to one live row per (org, workspace, platform).
            if data["platform"] in ACTION_ONLY_PLATFORMS:
                try:
                    IntegrationConnection.objects.get(
                        organization=organization,
                        workspace=workspace,
                        platform=data["platform"],
                    )
                    return _error_response(
                        f"{data['platform'].title()} is already connected for this workspace. "
                        "Edit the existing connection in Settings > Integrations to rotate keys."
                    )
                except IntegrationConnection.DoesNotExist:
                    pass

            # 5. Create connection
            connection = IntegrationConnection.objects.create(
                organization=organization,
                workspace=workspace,
                created_by=request.user,
                platform=data["platform"],
                display_name=data.get("display_name") or ext_project_name,
                host_url=host_url or f"https://{data['platform']}.com",
                encrypted_credentials=encrypted,
                ca_certificate=ca_cert or "",
                project=project,
                external_project_name=ext_project_name,
                status=initial_status,
                sync_interval_seconds=data.get("sync_interval_seconds", 300),
                backfill_from=backfill_from,
                backfill_completed=backfill_completed,
            )

            # 6. Start backfill workflow if needed
            if not backfill_completed:
                try:
                    from integrations.temporal.activities import (
                        start_backfill_workflow,
                    )

                    start_backfill_workflow(str(connection.id))
                except Exception as e:
                    logger.warning(
                        "Failed to start backfill workflow",
                        connection_id=str(connection.id),
                        error=str(e),
                    )

            result = IntegrationConnectionDetailSerializer(connection).data
            return _success_response(result, status.HTTP_201_CREATED)

        except IntegrityError:
            platform = (request.data or {}).get("platform")
            if platform in ACTION_ONLY_PLATFORMS:
                return _error_response(
                    f"{platform.title()} is already connected for this workspace. "
                    "Edit the existing connection in Settings > Integrations to rotate keys."
                )
            return _error_response(
                "A connection with these settings already exists in this workspace."
            )
        except Exception as e:
            logger.exception("Error creating integration connection", error=str(e))
            return _error_response(
                "Failed to create connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── UPDATE (PATCH) ─────────────────────────────────────────

    @validated_request(
        request_serializer=IntegrationConnectionUpdateSerializer,
        responses={
            200: IntegrationConnectionDetailResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    def update(self, request, *args, **kwargs):
        return self._update_connection(request)

    @validated_request(
        request_serializer=IntegrationConnectionUpdateSerializer,
        responses={
            200: IntegrationConnectionDetailResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    def partial_update(self, request, *args, **kwargs):
        return self._update_connection(request)

    def _update_connection(self, request):
        try:
            instance = self.get_object()
            data = request.validated_data

            # Update display_name if provided
            if "display_name" in data:
                instance.display_name = data["display_name"]

            # If keys are being updated, re-validate
            if "public_key" in data or "secret_key" in data:
                current_creds = CredentialManager.decrypt(
                    bytes(instance.encrypted_credentials)
                )
                new_creds = {**current_creds}
                if "public_key" in data:
                    new_creds["public_key"] = data["public_key"]
                if "secret_key" in data:
                    new_creds["secret_key"] = data["secret_key"]

                host_url = data.get("host_url", instance.host_url)
                ca_cert = data.get("ca_certificate", instance.ca_certificate) or None

                ensure_services_loaded()

                service = get_integration_service(instance.platform)
                validation = service.validate_credentials(
                    host_url=host_url,
                    credentials=new_creds,
                    ca_certificate=ca_cert,
                )

                if not validation.get("valid"):
                    return _error_response(
                        validation.get("error", "Invalid credentials.")
                    )

                instance.encrypted_credentials = CredentialManager.encrypt(new_creds)

                # Clear error state if keys were updated successfully
                if instance.status == ConnectionStatus.ERROR:
                    instance.status = ConnectionStatus.ACTIVE
                    instance.status_message = ""

            if "host_url" in data:
                instance.host_url = data["host_url"]
            if "ca_certificate" in data:
                instance.ca_certificate = data["ca_certificate"]
            if "sync_interval_seconds" in data:
                instance.sync_interval_seconds = data["sync_interval_seconds"]

            instance.save()

            result = IntegrationConnectionDetailSerializer(instance).data
            return _success_response(result)

        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception("Error updating integration connection", error=str(e))
            return _error_response(
                "Failed to update connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── DELETE (soft) ───────────────────────────────────────────

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete()  # BaseModel soft delete
            return _success_response({"deleted": True})
        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception("Error deleting integration connection", error=str(e))
            return _error_response(
                "Failed to delete connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── CUSTOM ACTIONS ──────────────────────────────────────────

    @validated_request(
        request_serializer=ValidateCredentialsSerializer,
        responses={
            200: IntegrationValidationResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"], url_path="validate")
    def validate_credentials(self, request):
        """Validate platform credentials without creating a connection."""
        try:
            data = request.validated_data
            logger.info(
                "Validate credentials request",
                platform=data["platform"],
                host_url=data["host_url"],
            )

            ensure_services_loaded()

            service = get_integration_service(data["platform"])
            credentials = _build_credentials(data)
            ca_cert = data.get("ca_certificate") or None

            result = service.validate_credentials(
                host_url=data.get("host_url") or "",
                credentials=credentials,
                ca_certificate=ca_cert,
            )

            logger.info(
                "Validate credentials result",
                valid=result.get("valid"),
                error=result.get("error"),
            )

            if result.get("valid"):
                return _success_response(result)
            else:
                return _error_response(result.get("error", "Invalid credentials."))

        except Exception as e:
            logger.exception("Error validating credentials", error=str(e))
            return _error_response(
                "Validation failed. Please check your credentials and try again."
            )

    @validated_request(
        request_serializer=IntegrationEmptyRequestSerializer,
        responses={
            200: IntegrationMessageResponseSerializer,
            **INTEGRATION_SYNC_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="sync_now")
    def sync_now(self, request, pk=None):
        """Trigger an immediate sync for this connection."""
        try:
            instance = self.get_object()

            if instance.status in (
                ConnectionStatus.SYNCING,
                ConnectionStatus.BACKFILLING,
            ):
                return _error_response(
                    "Connection is already syncing. Please wait for the current sync to complete.",
                    status.HTTP_409_CONFLICT,
                )

            if instance.status == ConnectionStatus.PAUSED:
                return _error_response(
                    "Connection is paused. Resume it before triggering a sync."
                )

            # Cooldown: prevent sync spam (min 60s between manual syncs)
            if instance.last_synced_at:
                elapsed = (datetime.now(UTC) - instance.last_synced_at).total_seconds()
                if elapsed < 60:
                    remaining = int(60 - elapsed)
                    return _error_response(
                        f"Please wait {remaining} seconds before triggering another sync."
                    )

            # Dispatch sync activity
            try:
                from integrations.temporal.activities import sync_integration_connection

                sync_integration_connection.delay(str(instance.id))
            except Exception as e:
                logger.warning(
                    "Failed to dispatch sync activity",
                    connection_id=str(instance.id),
                    error=str(e),
                )
                return _error_response("Failed to trigger sync. Please try again.")

            return _success_response({"message": "Sync triggered."})

        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception("Error triggering sync", error=str(e))
            return _error_response(
                "Failed to trigger sync.", status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @validated_request(
        request_serializer=IntegrationEmptyRequestSerializer,
        responses={
            200: IntegrationConnectionDetailResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="pause")
    def pause(self, request, pk=None):
        """Pause syncing for this connection."""
        try:
            instance = self.get_object()

            if instance.status == ConnectionStatus.PAUSED:
                return _error_response("Connection is already paused.")

            instance.status = ConnectionStatus.PAUSED
            instance.save(update_fields=["status", "updated_at"])

            result = IntegrationConnectionDetailSerializer(instance).data
            return _success_response(result)

        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception("Error pausing connection", error=str(e))
            return _error_response(
                "Failed to pause connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @validated_request(
        request_serializer=IntegrationEmptyRequestSerializer,
        responses={
            200: IntegrationConnectionDetailResponseSerializer,
            **INTEGRATION_ERROR_RESPONSES,
        },
        validation_error_response=_validation_error_response,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="resume")
    def resume(self, request, pk=None):
        """Resume syncing for a paused connection."""
        try:
            instance = self.get_object()

            if instance.status != ConnectionStatus.PAUSED:
                return _error_response("Only paused connections can be resumed.")

            # Check if the project still exists
            if not instance.project:
                return _error_response(
                    "The linked FutureAGI project has been deleted. "
                    "Please relink to a project before resuming."
                )

            instance.status = ConnectionStatus.ACTIVE
            instance.status_message = ""
            instance.save(update_fields=["status", "status_message", "updated_at"])

            result = IntegrationConnectionDetailSerializer(instance).data
            return _success_response(result)

        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.exception("Error resuming connection", error=str(e))
            return _error_response(
                "Failed to resume connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
