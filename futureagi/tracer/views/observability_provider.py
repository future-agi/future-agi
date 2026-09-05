import math

import structlog
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from accounts.utils import get_request_organization
from simulate.services.agent_definition import (
    is_masked,
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
from tracer.utils.otel import get_or_create_project

logger = structlog.get_logger(__name__)


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

            # VAPI/RETELL/BLAND support key verification; reject the rest clearly.
            if provider in (
                ProviderChoices.VAPI,
                ProviderChoices.RETELL,
                ProviderChoices.BLAND,
            ):
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

            # VAPI/RETELL/BLAND have an assistant/pathway to verify against.
            if provider in (
                ProviderChoices.VAPI,
                ProviderChoices.RETELL,
                ProviderChoices.BLAND,
            ):
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
