import json
import math
from typing import Any


def verify_retell_webhook(payload, *, api_key, signature):
    """Thin wrapper around retell.lib.webhook_auth.verify.

    Defined at module level so tests can patch
    tracer.views.observability_provider.verify_retell_webhook without
    needing the retell-sdk voice extra installed.
    """
    try:
        from retell.lib.webhook_auth import verify as _verify
    except ImportError as e:
        raise ImportError(
            "Retell webhook verification requires the `voice` extra. "
            "Install with: pip install 'core-backend[voice]'"
        ) from e
    return _verify(payload, api_key=api_key, signature=signature)


import structlog
from django.db import DatabaseError
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from accounts.utils import get_request_organization
from simulate.models import AgentDefinition
from simulate.services.agent_definition import (
    is_masked,
    resolve_api_key_for_version,
    resolve_stored_api_key,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.observability_provider import ProviderChoices
from tracer.models.project import ProjectSourceChoices
from tracer.serializers.observability_provider import (
    ObservabilityProviderSerializer,
    VerifyApiKeyRequestSerializer,
    VerifyAssistantIdRequestSerializer,
    VerifyResponseSerializer,
)
from tracer.services.observability_providers import ObservabilityService
from tracer.utils.observability_provider import normalize_and_store_logs
from tracer.utils.otel import get_or_create_project

logger = structlog.get_logger(__name__)

# Provider packages


class WebhookRequestSerializer(serializers.Serializer):
    call = serializers.JSONField()

    def validate_call(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("call must be an object.")
        if not value.get("agent_id"):
            raise serializers.ValidationError("call.agent_id is required.")
        return value


class WebhookResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField(default=True)
    result = serializers.CharField()


class ObservabilityProviderViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """
    API endpoints for managing Observability Providers.
    """

    serializer_class = ObservabilityProviderSerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get_queryset(self):
        queryset = super().get_queryset()
        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            total_count = queryset.count()

            page_number = int(request.query_params.get("page_number", 0))
            page_size = int(request.query_params.get("page_size", 20))

            start = page_number * page_size
            end = start + page_size

            total_pages = math.ceil(total_count / page_size)
            next_page_number = (
                page_number + 1 if (page_number + 1) < total_pages else None
            )

            paginated_queryset = queryset[start:end]
            serializer = self.get_serializer(paginated_queryset, many=True)

            response = {
                "metadata": {
                    "total_count": total_count,
                    "current_page": page_number,
                    "page_size": page_size,
                    "total_pages": total_pages,
                    "next_page": next_page_number,
                },
                "providers": serializer.data,
            }

            return self._gm.success_response(response)
        except Exception as e:
            logger.exception(f"Error listing observability providers: {e}")
            return self._gm.bad_request(
                get_error_message("ERROR_FETCHING_OBSERVABILITY_PROVIDERS")
            )

    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            project_name = serializer.validated_data["project_name"]

            _org = get_request_organization(request)
            workspace = getattr(request, "workspace", None)
            project = get_or_create_project(
                project_name=project_name,
                organization_id=_org.id if _org else None,
                project_type="observe",
                user_id=str(request.user.id),
                workspace_id=str(workspace.id) if workspace else None,
                source=ProjectSourceChoices.SIMULATOR.value,
            )

            serializer.save(
                project=project,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                workspace=workspace,
            )
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception(f"Error creating observability provider: {e}")
            return self._gm.bad_request(get_error_message("FAILED_TO_CREATE_PROVIDER"))

    def retrieve(self, request, *args, **kwargs):
        try:
            return super().retrieve(request, *args, **kwargs)
        except Exception as e:
            logger.exception(f"Error retrieving observability provider: {e}")
            return self._gm.bad_request(
                get_error_message("OBSERVABILITY_PROVIDER_NOT_FOUND")
            )

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=True)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            self.perform_update(serializer)
            return self._gm.success_response(serializer.data)
        except ValidationError as e:
            return self._gm.bad_request(e.detail)
        except Exception as e:
            logger.exception(f"Error updating observability provider: {e}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_UPDATE_OBSERVABILITY_PROVIDER")
            )

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return self._gm.success_response(
                "Observability provider deleted successfully."
            )
        except Exception as e:
            logger.exception(f"Error deleting observability provider: {e}")
            return self._gm.bad_request(
                get_error_message("FAILED_TO_DELETE_OBSERVABILITY_PROVIDER")
            )

    def perform_create(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
        )

    @validated_request(
        request_serializer=VerifyApiKeyRequestSerializer,
        responses={200: VerifyResponseSerializer, 400: ApiErrorResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def verify_api_key(self, request):
        try:
            provider = request.data.get("provider")
            api_key = request.data.get("api_key")
            agent_id = request.data.get("agent_id")

            if is_masked(api_key):
                api_key = resolve_stored_api_key(
                    organization=get_request_organization(request),
                    workspace=getattr(request, "workspace", None),
                    agent_id=agent_id,
                    masked_value=api_key,
                )
                if not api_key:
                    msg = "Could not resolve the api key. Please recheck the same"
                    return self._gm.bad_request(msg)

            # Only VAPI/RETELL support key verification; reject the rest clearly.
            if provider in (ProviderChoices.VAPI, ProviderChoices.RETELL):
                status_code = ObservabilityService.verify_api_key(
                    provider=provider,
                    api_key=api_key,
                )
                if status_code == 200:
                    return self._gm.success_response("API key verified successfully.")
                else:
                    return self._gm.bad_request("Invalid API key.")
            else:
                return self._gm.bad_request(
                    f"API key verification is not supported for provider: {provider}"
                )
        except Exception as e:
            logger.exception(f"Error verifying API key: {e}")
            return self._gm.bad_request(f"Error verifying API key: {e}")

    @validated_request(
        request_serializer=VerifyAssistantIdRequestSerializer,
        responses={200: VerifyResponseSerializer, 400: ApiErrorResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def verify_assistant_id(self, request):
        try:
            assistant_id = request.data.get("assistant_id")
            api_key = request.data.get("api_key")
            provider = request.data.get("provider")
            agent_id = request.data.get("agent_id")
            if is_masked(api_key):
                api_key = resolve_stored_api_key(
                    organization=get_request_organization(request),
                    workspace=getattr(request, "workspace", None),
                    agent_id=agent_id,
                    assistant_id=assistant_id,
                    masked_value=api_key,
                )
                if not api_key:
                    msg = "Could not resolve the api key. Please recheck the same"
                    return self._gm.bad_request(msg)

            # Only VAPI/RETELL have an assistant model to verify against.
            if provider in (ProviderChoices.VAPI, ProviderChoices.RETELL):
                status_code = ObservabilityService.verify_assistant_id(
                    provider=provider,
                    assistant_id=assistant_id,
                    api_key=api_key,
                )
                if status_code == 200:
                    return self._gm.success_response(
                        "Assistant ID verified successfully."
                    )
                else:
                    return self._gm.bad_request("Invalid assistant ID.")
            else:
                return self._gm.bad_request(
                    f"Assistant ID verification is not supported for provider: {provider}"
                )
        except Exception as e:
            logger.exception(f"Error verifying assistant ID: {e}")
            return self._gm.bad_request(f"Error verifying assistant ID: {e}")


class WebhookHandlerView(APIView):
    _gm = GeneralMethods()
    authentication_classes: list[Any] = []  # Disable authentication for webhook
    permission_classes: list[Any] = []  # Disable permission checks

    def get_api_key(self, agent_definition: AgentDefinition):
        try:
            if not agent_definition:
                return None

            agent_version = agent_definition.latest_version
            if not agent_version:
                logger.warning(
                    f"No agent version found for agent {agent_definition.id}"
                )
                return None

            return resolve_api_key_for_version(agent_version)
        except Exception as e:
            logger.exception(f"Error getting webhook secret: {e}")
            return None

    @validated_request(
        request_serializer=WebhookRequestSerializer,
        responses={
            200: WebhookResponseSerializer,
            400: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
        },
    )
    def post(self, request):
        try:
            post_data = request.data
            headers = request.headers

            matched_count = 0
            processed_count = 0
            failed_count = 0

            call = request.validated_data["call"]
            agent_id = call["agent_id"]

            try:
                agent_definitions = list(
                    _matching_agent_definitions_for_webhook(agent_id).iterator(
                        chunk_size=500
                    )
                )
            except Exception:
                logger.exception("webhook_agent_lookup_unavailable")
                return self._gm.custom_error_response(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "Webhook agent lookup is temporarily unavailable.",
                    code="service_unavailable",
                )

            for agent_definition in agent_definitions:
                matched_count += 1

                # Retrieve webhook secret from agent version for agent_definition
                api_key = self.get_api_key(agent_definition=agent_definition)

                if not api_key:
                    failed_count += 1
                    error_message = f"No API key for agent: {agent_definition.id}"
                    logger.warning(error_message)

                    continue

                valid_signature = verify_retell_webhook(
                    json.dumps(post_data, separators=(",", ":"), ensure_ascii=False),
                    api_key=api_key,
                    signature=str(headers.get("X-Retell-Signature") or ""),
                )

                if not valid_signature:
                    failed_count += 1
                    logger.warning(
                        "Invalid webhook signature for agent definition",
                        agent_definition_id=str(agent_definition.id),
                    )
                    continue

                try:
                    normalize_and_store_logs.delay(
                        body=post_data,
                        agent_definition_id=agent_definition.id,
                    )
                except Exception:
                    logger.exception(
                        "webhook_log_dispatch_failed_running_inline",
                        agent_definition_id=str(agent_definition.id),
                    )
                    normalize_and_store_logs.run_sync(
                        body=post_data,
                        agent_definition_id=agent_definition.id,
                    )
                processed_count += 1

            if matched_count == 0:
                logger.error("No matching agent definition found")
                return self._gm.bad_request("No matching agent definition found")

            if processed_count == 0:
                logger.error("No valid webhook signature found")
                return self._gm.bad_request("Invalid webhook signature")

            return self._gm.success_response(
                f"Logs processed successfully. \nProcessed: {processed_count} \nFailed: {failed_count}"
            )

        except DatabaseError:
            logger.exception("webhook_agent_lookup_database_unavailable")
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Webhook agent lookup is temporarily unavailable.",
                code="service_unavailable",
            )
        except Exception as e:
            logger.exception(f"Error in webhook handler: {e}")
            return self._gm.bad_request("Error processing webhook")


def _matching_agent_definitions_for_webhook(agent_id):
    return AgentDefinition.no_workspace_objects.select_related(
        "observability_provider"
    ).filter(
        assistant_id=agent_id,
        observability_provider__enabled=True,
        observability_provider__deleted=False,
    )
