import json
import re
import uuid
from datetime import datetime

import structlog
from django.db import models, transaction
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from retell import Retell

logger = structlog.get_logger(__name__)
from simulate.models import AgentDefinition, AgentVersion
from simulate.serializers.requests.agent_definition import (
    AgentDefinitionBulkDeleteRequestSerializer,
    AgentDefinitionCreateRequestSerializer,
    AgentDefinitionEditRequestSerializer,
    AgentDefinitionFilterSerializer,
    FetchAssistantRequestSerializer,
)
from simulate.serializers.response.agent_definition import (
    AgentDefinitionBulkDeleteResponseSerializer,
    AgentDefinitionCreateResponseSerializer,
    AgentDefinitionDeleteResponseSerializer,
    AgentDefinitionEditResponseSerializer,
    AgentDefinitionListResponseSerializer,
    AgentDefinitionResponseSerializer,
    FetchAssistantResponseSerializer,
)
from simulate.serializers.response.agent_version import (
    AgentVersionListResponseSerializer,
)
try:
    from ee.voice.services.vapi_service import VapiService
except ImportError:
    VapiService = None
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.models.observability_provider import ProviderChoices
from tracer.models.replay_session import ReplaySession
from tracer.utils.observability_provider import create_observability_provider
from tracer.utils.otel import ResourceLimitError
from tracer.utils.replay_session import link_agent_to_replay_session


class AgentDefinitionView(APIView):
    """
    API View to list agent definitions for an organization with pagination and search,
    and to bulk-delete agent definitions.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        query_serializer=AgentDefinitionFilterSerializer,
        responses={200: AgentDefinitionListResponseSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        """
        Get paginated list of agent definitions for the user's organization.
        """
        try:
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Validate query parameters through serializer
            filter_serializer = AgentDefinitionFilterSerializer(
                data=request.query_params
            )
            if not filter_serializer.is_valid():
                return self._gm.bad_request(filter_serializer.errors)

            validated = filter_serializer.validated_data
            search_query = validated.get("search", "").strip()
            agent_type = validated.get("agent_type", None)
            required_agent_id = validated.get("agent_definition_id", None)

            # Use Subquery to get latest version info in a single query (avoid N+1)
            from django.db.models import OuterRef, Subquery

            latest_version_subquery = (
                AgentVersion.objects.filter(agent_definition=OuterRef("pk"))
                .order_by("-version_number")
                .values("version_number")[:1]
            )
            latest_version_id_subquery = (
                AgentVersion.objects.filter(agent_definition=OuterRef("pk"))
                .order_by("-version_number")
                .values("id")[:1]
            )

            agents = AgentDefinition.objects.filter(
                organization=user_organization,
            ).annotate(
                _latest_version=Subquery(latest_version_subquery),
                _latest_version_id=Subquery(latest_version_id_subquery),
            )

            if agent_type is not None:
                agents = agents.filter(agent_type=agent_type)

            # Apply search filter
            if search_query:
                pattern = rf"(?i){re.escape(search_query)}"
                agents = agents.filter(
                    models.Q(agent_name__regex=pattern)
                    | models.Q(contact_number__regex=pattern)
                    | models.Q(description__regex=pattern)
                    | models.Q(assistant_id__regex=pattern)
                )

            # If required_agent_id is provided, fetch it separately to place it first
            required_agent = None
            if required_agent_id:
                required_agent_id_str = str(required_agent_id)
                try:
                    required_agent = agents.get(id=required_agent_id_str, deleted=False)
                except AgentDefinition.DoesNotExist:
                    pass

            page = int(request.query_params.get("page", 1))
            if required_agent and page == 1:
                agents = agents.exclude(id=required_agent.id).order_by("-created_at")
                paginator = ExtendedPageNumberPagination()
                result_page = paginator.paginate_queryset(agents, request)
                paginator.page.paginator.count += 1
                result_page = [required_agent] + result_page[
                    : paginator.get_page_size(request) - 1
                ]
            else:
                if required_agent:
                    agents = agents.exclude(id=required_agent.id)
                agents = agents.order_by("-created_at")
                paginator = ExtendedPageNumberPagination()
                result_page = paginator.paginate_queryset(agents, request)

            serializer = AgentDefinitionListResponseSerializer(result_page, many=True)
            return paginator.get_paginated_response(serializer.data)

        except NotFound:
            raise
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve agent definitions: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        request_body=AgentDefinitionBulkDeleteRequestSerializer,
        responses={200: AgentDefinitionBulkDeleteResponseSerializer},
    )
    def delete(self, request):
        """
        Bulk soft-delete agent definitions.
        """
        try:
            serializer = AgentDefinitionBulkDeleteRequestSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            agent_ids = serializer.validated_data["agent_ids"]

            with transaction.atomic():
                updated_agents = AgentDefinition.objects.filter(
                    id__in=agent_ids,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                ).update(deleted=True, deleted_at=timezone.now())

                updated_versions = AgentVersion.objects.filter(
                    agent_definition_id__in=agent_ids,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                ).update(deleted=True, deleted_at=timezone.now())

            response_data = {
                "message": "Agents deleted successfully",
                "agents_updated": updated_agents,
                "versions_updated": updated_versions,
            }
            return Response(
                AgentDefinitionBulkDeleteResponseSerializer(response_data).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to delete agents: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateAgentDefinitionView(APIView):
    """
    API View to create a new agent definition.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        request_body=AgentDefinitionCreateRequestSerializer,
        responses={201: AgentDefinitionCreateResponseSerializer},
    )
    def post(self, request, *args, **kwargs):
        """
        Create a new agent definition with its first version.
        """
        try:
            # Validate request through serializer
            req_serializer = AgentDefinitionCreateRequestSerializer(data=request.data)
            if not req_serializer.is_valid():
                return Response(
                    {"error": "Invalid data", "details": req_serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            validated = req_serializer.validated_data
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            workspace = getattr(request.user, "workspace", None)
            user_id = str(request.user.id)
            commit_message = validated["commit_message"]
            description = validated.get("description", "")
            enable_observability = validated.get("observability_enabled", False)
            project_name = validated.get("agent_name")
            assistant_id = validated.get("assistant_id")
            api_key = validated.get("api_key")
            provider = validated.get("provider")
            replay_session_id = validated.get("replay_session_id")
            observability_provider = None

            if (
                enable_observability
                and assistant_id != ""
                and api_key != ""
                and provider
                in [
                    ProviderChoices.VAPI,
                    ProviderChoices.RETELL,
                    ProviderChoices.OTHERS,
                ]
            ):
                observability_provider = create_observability_provider(
                    enabled=True,
                    user_id=user_id,
                    organization=organization,
                    workspace=workspace,
                    project_name=project_name,
                    provider=provider,
                )

            # Create agent definition — livekit_* fields are NOT model
            # columns, they're routed to ProviderCredentials below.
            agent = AgentDefinition.objects.create(
                agent_name=validated["agent_name"],
                agent_type=validated["agent_type"],
                description=description,
                provider=provider,
                api_key=api_key,
                assistant_id=assistant_id,
                authentication_method=validated.get("authentication_method") or "",
                language=validated.get("language"),
                languages=validated.get("languages") or ["en"],
                contact_number=validated.get("contact_number"),
                inbound=validated.get("inbound", True),
                knowledge_base_id=validated.get("knowledge_base"),
                model=validated.get("model"),
                model_details=validated.get("model_details") or {},
                websocket_url=validated.get("websocket_url"),
                websocket_headers=validated.get("websocket_headers") or {},
                organization=organization,
                workspace=workspace,
                observability_provider=observability_provider,
            )

            # Route livekit/provider credentials to ProviderCredentials table.
            from simulate.serializers.agent_definition import (
                AgentDefinitionSerializer,
                ProviderCredentialsInput,
            )

            creds_input = ProviderCredentialsInput(
                provider=provider or "",
                api_key=api_key,
                assistant_id=assistant_id,
                livekit_url=validated.get("livekit_url"),
                livekit_api_key=validated.get("livekit_api_key"),
                livekit_api_secret=validated.get("livekit_api_secret"),
                livekit_agent_name=validated.get("livekit_agent_name"),
                livekit_config_json=validated.get("livekit_config_json"),
                livekit_max_concurrency=validated.get("livekit_max_concurrency"),
            )
            AgentDefinitionSerializer._sync_provider_credentials(agent, creds_input)

            # Create the first version
            agent.create_version(
                description=description,
                commit_message=commit_message,
                status=AgentVersion.StatusChoices.ACTIVE,
            )

            if replay_session_id:
                link_agent_to_replay_session(
                    replay_session_id=replay_session_id,
                    agent=agent,
                    organization=organization,
                )

            response_data = {
                "message": "Agent definition created successfully",
                "agent": AgentDefinitionResponseSerializer(agent).data,
            }
            return Response(response_data, status=status.HTTP_201_CREATED)

        except ReplaySession.DoesNotExist:
            return self._gm.not_found(get_error_message("REPLAY_SESSION_NOT_FOUND"))
        except ResourceLimitError as e:
            return self._gm.bad_request("PROJECT CREATION LIMIT REACHED")
        except Exception as e:
            return Response(
                {"error": f"Failed to create agent definition: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AgentDefinitionDetailView(APIView):
    """
    API View to retrieve a specific agent definition with version history.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: AgentDefinitionResponseSerializer},
    )
    def get(self, request, agent_id, *args, **kwargs):
        """
        Get details of a specific agent definition with version information.
        """
        try:
            agent = AgentDefinition.objects.select_related("credentials").get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            agent_data = AgentDefinitionResponseSerializer(agent).data

            # Get version information
            versions = agent.get_version_history()
            versions_data = AgentVersionListResponseSerializer(versions, many=True).data

            # Get active version
            active_version = agent.active_version
            active_version_data = None
            if active_version:
                active_version_data = AgentVersionListResponseSerializer(
                    active_version
                ).data

            return Response(
                {
                    **agent_data,
                    "versions": versions_data,
                    "active_version": active_version_data,
                    "version_count": agent.version_count,
                },
                status=status.HTTP_200_OK,
            )

        except AgentDefinition.DoesNotExist:
            return Response(
                {"error": "Agent definition not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve agent definition: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AgentDefinitionOperationsViewSet(BaseModelViewSetMixin, ModelViewSet):
    permissions = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = AgentDefinitionResponseSerializer

    def get_queryset(self):
        # select_related("credentials") avoids N+1 when
        # AgentDefinitionSerializer.to_representation reads
        # instance.credentials (OneToOne reverse accessor).
        return super().get_queryset().select_related("credentials")

    @swagger_auto_schema(
        request_body=FetchAssistantRequestSerializer,
        responses={200: FetchAssistantResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def fetch_assistant_from_provider(self, request):
        """
        Fetches the details of agent from the provider and sends them to the client.
        It DOES NOT create a new version.
        """

        try:
            serializer = FetchAssistantRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            validated = serializer.validated_data

            api_key = validated["api_key"]
            provider = validated["provider"]
            assistant_id = validated["assistant_id"]
            prompt = ""
            name = ""

            if provider == ProviderChoices.VAPI:
                from tfc.ee_gating import EEFeature, check_ee_feature

                org = getattr(request, "organization", None) or request.user.organization
                check_ee_feature(
                    EEFeature.VOICE_SIM,
                    org_id=str(org.id) if org else None,
                )
                vapi_service = VapiService(api_key=api_key)
                assistant_json = vapi_service.get_assistant(assistant_id=assistant_id)

                model = assistant_json.get("model")
                messages = model.get("messages")
                system_object = [
                    message for message in messages if message.get("role") == "system"
                ][0]

                name = assistant_json.get("name")
                prompt = system_object.get("content")

            elif provider == ProviderChoices.RETELL:
                client = Retell(api_key=api_key)

                assistant_raw = client.agent.retrieve(
                    agent_id=assistant_id
                ).model_dump_json()
                assistant_json = json.loads(assistant_raw)
                response_engine = assistant_json.get("response_engine")
                llm_id = response_engine.get("llm_id")

                response_engine_raw = client.llm.retrieve(
                    llm_id=llm_id
                ).model_dump_json()
                response_engine_json = json.loads(response_engine_raw)
                name = assistant_json.get("agent_name")
                prompt = response_engine_json.get("general_prompt")

            response_data = {
                "assistant_id": assistant_id,
                "api_key": api_key,
                "name": name,
                "prompt": prompt,
                "provider": provider,
                "commit_message": f"Synced at {datetime.now().strftime('%A, %B %d, %Y %I:%M %p')}",
            }

            return self._gm.success_response(
                FetchAssistantResponseSerializer(response_data).data
            )

        except FeatureUnavailable:
            raise
        except Exception:
            logger.exception("fetch_assistant_from_provider failed")
            return self._gm.bad_request("Please recheck your API key and assistant ID")


class EditAgentDefinitionView(APIView):
    """
    API View to edit an existing agent definition.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=AgentDefinitionEditRequestSerializer,
        responses={200: AgentDefinitionEditResponseSerializer},
    )
    def put(self, request, agent_id, *args, **kwargs):
        """
        Update an existing agent definition.
        """
        try:
            agent = AgentDefinition.objects.select_related("credentials").get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Validate request through request serializer
            req_serializer = AgentDefinitionEditRequestSerializer(data=request.data)
            if not req_serializer.is_valid():
                return Response(
                    {"error": "Invalid data", "details": req_serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Update agent fields directly from validated data. NOTE:
            # ``livekit_*`` fields are NOT model columns on AgentDefinition;
            # they live on the related ProviderCredentials row and are
            # routed below via ``_sync_provider_credentials``.
            validated = req_serializer.validated_data
            update_fields = [
                "agent_name",
                "agent_type",
                "description",
                "provider",
                "api_key",
                "assistant_id",
                "authentication_method",
                "language",
                "languages",
                "contact_number",
                "inbound",
                "model",
                "model_details",
                "websocket_url",
                "websocket_headers",
            ]
            for field in update_fields:
                if field in validated:
                    setattr(agent, field, validated[field])
            if "knowledge_base" in validated:
                agent.knowledge_base_id = validated["knowledge_base"]
            agent.save()

            # Route livekit_* fields to ProviderCredentials so they
            # actually persist (setattr on the model is a no-op for these
            # since they aren't real columns).
            from simulate.serializers.agent_definition import (
                AgentDefinitionSerializer,
                ProviderCredentialsInput,
            )

            creds_input = ProviderCredentialsInput(
                provider=validated.get("provider") or agent.provider or "",
                api_key=validated.get("api_key"),
                assistant_id=validated.get("assistant_id"),
                livekit_url=validated.get("livekit_url"),
                livekit_api_key=validated.get("livekit_api_key"),
                livekit_api_secret=validated.get("livekit_api_secret"),
                livekit_agent_name=validated.get("livekit_agent_name"),
                livekit_config_json=validated.get("livekit_config_json"),
                livekit_max_concurrency=validated.get("livekit_max_concurrency"),
            )
            AgentDefinitionSerializer._sync_provider_credentials(agent, creds_input)
            try:
                del agent.credentials
            except AttributeError:
                pass
            updated_agent = agent

            response_data = {
                "message": "Agent definition updated successfully",
                "agent": AgentDefinitionResponseSerializer(updated_agent).data,
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except AgentDefinition.DoesNotExist:
            return Response(
                {"error": "Agent definition not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to update agent definition: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeleteAgentDefinitionView(APIView):
    """
    API View to delete an agent definition (soft delete).
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: AgentDefinitionDeleteResponseSerializer},
    )
    def delete(self, request, agent_id, *args, **kwargs):
        """
        Soft delete an agent definition.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            agent.delete()

            response_data = {"message": "Agent definition deleted successfully"}
            return Response(
                AgentDefinitionDeleteResponseSerializer(response_data).data,
                status=status.HTTP_200_OK,
            )

        except AgentDefinition.DoesNotExist:
            return Response(
                {"error": "Agent definition not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to delete agent definition: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
