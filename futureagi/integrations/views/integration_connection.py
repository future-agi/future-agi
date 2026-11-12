import ipaddress
import math
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import structlog
from django.db import IntegrityError
from django.db.models import Prefetch
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
    EXPORT_PLATFORMS,
    HOST_URL_PLATFORMS,
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
)
from integrations.permissions import CanManageIntegrations
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
from integrations.services.disconnect import disconnect_integration
from integrations.services.export_destinations import fixed_destination
from integrations.services.export_failures import exception_fields
from integrations.services.registry import ensure_services_loaded
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tracer.models.project import Project, ProjectSourceChoices
from tracer.utils.otel import get_or_create_project

logger = structlog.get_logger(__name__)

# Mixpanel's /track accepts events from the last 5 days
# (https://docs.mixpanel.com/reference/track-event).
MIXPANEL_TRACK_DAYS = 5
# Datadog's log intake accepts logs from the last 18 hours
# (integrations/temporal/activities.py DATADOG_LOG_MAX_AGE).
DATADOG_LOG_HOURS = 18
INITIAL_DISPATCH_FAILED = (
    "The first sync could not be started. We will retry "
    "automatically, or choose Sync Now to retry now."
)


def _with_dispatch_status(connection, data):
    # The general diagnostic projection points at Sync History. An initial
    # dispatch failure has no sync log, so expose only this fixed safe note.
    if (
        connection.status == ConnectionStatus.ERROR
        and connection.status_message == INITIAL_DISPATCH_FAILED
    ):
        data["status_message"] = INITIAL_DISPATCH_FAILED
    return data

# The credentials Edit replaces on an export, by the keys its wizard step
# stores them under; the rest (the Datadog site, the bucket, region and
# prefix) decide where it delivers and are set when it is added.
EDITABLE_CREDENTIALS: dict[str, tuple[str, ...]] = {
    IntegrationPlatform.DATADOG: ("api_key",),
    IntegrationPlatform.POSTHOG: ("api_key",),
    IntegrationPlatform.MIXPANEL: ("project_token", "api_secret"),
    IntegrationPlatform.CLOUD_STORAGE: ("access_key_id", "secret_access_key"),
}


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


_UNREACHABLE_HOST_MESSAGE = "This host URL cannot be used for an integration."


def _unreachable_host_error(host_url) -> str | None:
    """An error when ``host_url`` names a host the server must never reach.

    Applies ``tfc.utils.ssrf_guard``'s never-reachable rule (loopback,
    link-local and cloud metadata addresses) to an IP-literal host and to the
    local and metadata host names, without a DNS lookup. Private network
    addresses stay allowed, as for an operator's self-hosted service.
    """
    from tfc.utils.ssrf_guard import METADATA_HOSTNAMES, IPClass, classify_ip

    if not host_url:
        return None
    try:
        host = urlparse(str(host_url)).hostname
    except ValueError:
        return _UNREACHABLE_HOST_MESSAGE
    if not host:
        return None
    host = host.rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost") or host in METADATA_HOSTNAMES:
        return _UNREACHABLE_HOST_MESSAGE
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if classify_ip(address) is IPClass.NEVER:
        return _UNREACHABLE_HOST_MESSAGE
    return None


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
    permission_classes = [IsAuthenticated, CanManageIntegrations]
    # Adding, changing and removing a connection: org Owner or Admin, or an
    # Admin of the workspace. Listing, reading and Sync now stay open.
    integration_admin_actions = frozenset(
        {
            "create",
            "validate_credentials",
            "update",
            "partial_update",
            "destroy",
            "pause",
            "resume",
        }
    )

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .prefetch_related(
                Prefetch(
                    "sync_logs",
                    queryset=SyncLog.objects.filter(deleted=False).order_by(
                        "-started_at"
                    )[:1],
                    to_attr="latest_sync_log",
                )
            )
        )

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
                    "connections": [
                        _with_dispatch_status(connection, data)
                        for connection, data in zip(paginated_queryset, serializer.data)
                    ],
                }
            )
        except Exception as e:
            logger.warning(
                "Error listing integration connections", **exception_fields(e)
            )
            return _error_response(
                "Failed to list integration connections.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── RETRIEVE ────────────────────────────────────────────────

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = IntegrationConnectionDetailSerializer(instance)
            data = _with_dispatch_status(instance, serializer.data)
            logger.info(
                "Retrieve connection detail",
                connection_id=str(instance.id),
            )
            return _success_response(data)
        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.warning(
                "Error retrieving integration connection", **exception_fields(e)
            )
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

            host_error = _unreachable_host_error(data.get("host_url"))
            if host_error:
                return _error_response(host_error)

            ensure_services_loaded()

            platform = data["platform"]
            credentials = _build_credentials(data)
            ca_cert = data.get("ca_certificate") or None
            # The connection stores where it delivers: the address the user
            # entered for a Langfuse or PostHog instance, otherwise the
            # destination its credentials name.
            destination = fixed_destination(platform, credentials)
            if destination is not None:
                host_url = destination
            else:
                host_url = data.get("host_url") or ""
                if platform in HOST_URL_PLATFORMS and not host_url:
                    return _error_response(
                        f"host_url: Enter the address of your "
                        f"{IntegrationPlatform(platform).label} instance."
                    )

            # Mixpanel's /track, used without an API secret, accepts events
            # from the last 5 days only.
            backfill_from_date = data.get("backfill_from_date")
            if (
                platform == IntegrationPlatform.MIXPANEL
                and not credentials.get("api_secret")
                and data.get("backfill_option") == "from_date"
                and backfill_from_date
                and backfill_from_date
                < datetime.now(UTC) - timedelta(days=MIXPANEL_TRACK_DAYS)
            ):
                return _error_response(
                    "backfill_from_date: Without an API secret, Mixpanel accepts "
                    f"request logs from the last {MIXPANEL_TRACK_DAYS} days. Add "
                    "the API secret to export older ones."
                )

            # Datadog's log intake accepts logs from the last 18 hours.
            if (
                platform == IntegrationPlatform.DATADOG
                and data.get("backfill_option") == "from_date"
                and backfill_from_date
                and backfill_from_date
                < datetime.now(UTC) - timedelta(hours=DATADOG_LOG_HOURS)
            ):
                return _error_response(
                    "backfill_from_date: Datadog accepts logs from the last "
                    f"{DATADOG_LOG_HOURS} hours. Pick a start within them."
                )

            # 1. Validate credentials against the platform
            service = get_integration_service(platform)

            validation = service.validate_credentials(
                host_url=host_url,
                credentials=credentials,
                ca_certificate=ca_cert,
            )

            if not validation.get("valid"):
                return _error_response(validation.get("error", "Invalid credentials."))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            is_action_only = data["platform"] in ACTION_ONLY_PLATFORMS
            if (
                is_action_only
                and IntegrationConnection.objects.filter(
                    organization=organization,
                    workspace=workspace,
                    platform=data["platform"],
                ).exists()
            ):
                return _error_response(
                    f"{data['platform'].title()} is already connected for this workspace. "
                    "Disconnect it in Settings > Integrations to connect another key."
                )

            # Resolve external project name from request or validation
            ext_project_name = data.get("external_project_name") or ""
            if not ext_project_name:
                # Fallback: first project from validation, or platform name
                projects = validation.get("projects", [])
                ext_project_name = projects[0]["name"] if projects else data["platform"]

            # 2. Encrypt credentials
            encrypted = CredentialManager.encrypt(credentials)

            # 3. Resolve FutureAGI project. An export sends the gateway's
            # request logs out and an action-only platform receives alerts or
            # issues: neither imports into a project.
            project = None
            project_id = data.get("project_id")
            if is_action_only or platform in EXPORT_PLATFORMS:
                project = None
            elif project_id:
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

            if not is_action_only and backfill_option == "all":
                initial_status = ConnectionStatus.BACKFILLING
                backfill_completed = False
            elif not is_action_only and backfill_option == "from_date":
                initial_status = ConnectionStatus.BACKFILLING
                backfill_from = data.get("backfill_from_date")
                backfill_completed = False

            # 5. Create connection
            connection = IntegrationConnection.objects.create(
                organization=organization,
                workspace=workspace,
                created_by=request.user,
                platform=data["platform"],
                display_name=data.get("display_name")
                or (
                    IntegrationPlatform(platform).label
                    if ext_project_name == platform
                    else ext_project_name
                ),
                host_url=host_url,
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
                        **exception_fields(e),
                    )
                    # No task is running. Keep the backfill settings, but make
                    # the saved connection retryable by polling and Sync Now.
                    connection.status = ConnectionStatus.ERROR
                    connection.status_message = INITIAL_DISPATCH_FAILED
                    connection.save(
                        update_fields=["status", "status_message", "updated_at"]
                    )

            result = _with_dispatch_status(
                connection, IntegrationConnectionDetailSerializer(connection).data
            )
            return _success_response(result, status.HTTP_201_CREATED)

        except IntegrityError:
            platform = (request.data or {}).get("platform")
            if platform in ACTION_ONLY_PLATFORMS:
                return _error_response(
                    f"{platform.title()} is already connected for this workspace. "
                    "Disconnect it in Settings > Integrations to connect another key."
                )
            return _error_response(
                "A connection with these settings already exists in this workspace."
            )
        except Exception as e:
            logger.warning(
                "Error creating integration connection", **exception_fields(e)
            )
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
            if instance.platform == IntegrationPlatform.SLACK:
                return _error_response(
                    "Reconnect Slack through OAuth to change this connection."
                )
            data = request.validated_data
            update_fields = ["updated_at"]

            if "host_url" in data and instance.platform not in HOST_URL_PLATFORMS:
                return _error_response(
                    f"host_url: A {instance.get_platform_display()} connection "
                    "delivers where its credentials point; it has no host to change."
                )
            host_error = _unreachable_host_error(data.get("host_url"))
            if host_error:
                return _error_response(host_error)

            # Update display_name if provided
            if "display_name" in data:
                instance.display_name = data["display_name"]
                update_fields.append("display_name")

            replaced = data.get("credentials") or {}
            if replaced:
                editable = EDITABLE_CREDENTIALS.get(instance.platform, ())
                refused = sorted(set(replaced) - set(editable))
                if refused or not all(replaced.values()):
                    return _error_response(
                        f"credentials: A {instance.get_platform_display()} "
                        "connection replaces only "
                        f"{', '.join(editable) or 'no credentials'} here, each "
                        "with a value."
                    )

            keys_changed = "public_key" in data or "secret_key" in data or bool(replaced)
            # A new key, host or certificate is checked with the platform, as the
            # Add Integration wizard checks them, before it is saved; a new host
            # or certificate is checked with the stored keys.
            endpoint_changed = (
                "host_url" in data and data["host_url"] != instance.host_url
            ) or (
                "ca_certificate" in data
                and (data["ca_certificate"] or None)
                != (instance.ca_certificate or None)
            )

            # If keys or the endpoint are being updated, re-validate
            if keys_changed or endpoint_changed:
                current_creds = CredentialManager.decrypt(
                    bytes(instance.encrypted_credentials)
                )
                if (
                    instance.platform == IntegrationPlatform.CLOUD_STORAGE
                    and replaced
                    and current_creds.get("sub_platform") != "s3"
                ):
                    return _error_response(
                        "credentials: Only an Amazon S3 connection replaces its "
                        "keys here."
                    )
                new_creds = {**current_creds, **replaced}
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

                if keys_changed:
                    instance.encrypted_credentials = CredentialManager.encrypt(
                        new_creds
                    )
                    update_fields.append("encrypted_credentials")

                # A connection in error that passes the check is active again.
                if instance.status == ConnectionStatus.ERROR:
                    instance.status = ConnectionStatus.ACTIVE
                    instance.status_message = ""
                    update_fields.extend(["status", "status_message"])

            if "host_url" in data:
                instance.host_url = data["host_url"]
                update_fields.append("host_url")
            if "ca_certificate" in data:
                instance.ca_certificate = data["ca_certificate"]
                update_fields.append("ca_certificate")
            if "sync_interval_seconds" in data:
                instance.sync_interval_seconds = data["sync_interval_seconds"]
                update_fields.append("sync_interval_seconds")

            # An export may commit while this Edit waits for its row lock.
            # Persist only intentional edits, never the stale delivery state.
            instance.save(update_fields=update_fields)

            result = IntegrationConnectionDetailSerializer(instance).data
            return _success_response(result)

        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.warning(
                "Error updating integration connection", **exception_fields(e)
            )
            return _error_response(
                "Failed to update connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ─── DELETE (soft) ───────────────────────────────────────────

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            disconnect_integration(instance)
            return _success_response({"deleted": True})
        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.warning(
                "Error deleting integration connection", **exception_fields(e)
            )
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
            )

            host_error = _unreachable_host_error(data.get("host_url"))
            if host_error:
                return _error_response(host_error)

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
            logger.warning("Error validating credentials", **exception_fields(e))
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
        """Sync this connection now; on a connection in Error, retry it.

        Answers 200 only when a sync was dispatched; otherwise it refuses
        with the reason (action-only, already syncing, paused, or within a
        minute of the last attempt)."""
        try:
            instance = self.get_object()

            if instance.platform in ACTION_ONLY_PLATFORMS:
                return _error_response("Action-only integrations do not support sync.")

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

            retry = instance.status == ConnectionStatus.ERROR

            # Cooldown: at least 60 s since the last attempt, failed or not.
            attempts = [instance.last_synced_at]
            latest = (
                SyncLog.objects.filter(connection=instance)
                .order_by("-started_at")
                .values_list("started_at", flat=True)
                .first()
            )
            attempts.append(latest)
            last_attempt = max((t for t in attempts if t), default=None)
            if last_attempt:
                elapsed = (datetime.now(UTC) - last_attempt).total_seconds()
                if elapsed < 60:
                    remaining = max(1, math.ceil(60 - elapsed))
                    again = "retrying" if retry else "triggering another sync"
                    return _error_response(
                        f"Please wait {remaining} seconds before {again}."
                    )

            # Dispatch sync activity
            try:
                from integrations.temporal.activities import sync_integration_connection

                if retry:
                    sync_integration_connection.delay(str(instance.id), retry=True)
                else:
                    sync_integration_connection.delay(str(instance.id))
            except Exception as e:
                logger.warning(
                    "Failed to dispatch sync activity",
                    connection_id=str(instance.id),
                    **exception_fields(e),
                )
                return _error_response("Failed to trigger sync. Please try again.")

            return _success_response(
                {"message": "Retry started." if retry else "Sync triggered."}
            )

        except Http404:
            return _error_response(
                "Integration connection not found.", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            logger.warning("Error triggering sync", **exception_fields(e))
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
            logger.warning("Error pausing connection", **exception_fields(e))
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

            # An import needs the project it imports into; an export has none.
            if instance.platform not in EXPORT_PLATFORMS and not instance.project:
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
            logger.warning("Error resuming connection", **exception_fields(e))
            return _error_response(
                "Failed to resume connection.",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
