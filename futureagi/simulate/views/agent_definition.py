import json
import re
from datetime import datetime

import requests
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

from simulate.models import AgentDefinition, AgentVersion
from simulate.serializers.agent_definition import AgentDefinitionSerializer
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
from simulate.services.agent_definition import (
    is_masked,
    resolve_api_key_for_version,
    resolve_stored_api_key,
    sync_provider_credentials,
)
from simulate.services.types.agent_definition import ProviderCredentialsInput
from tfc.ee_stub import _ee_stub

try:
    from ee.voice.services.vapi_service import VapiService
except ImportError:
    VapiService = _ee_stub("VapiService")
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorWithDetailsResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.constants.external_endpoints import ObservabilityRoutes
from tracer.models.observability_provider import ProviderChoices
from tracer.models.replay_session import ReplaySession
from tracer.services.observability_providers import (
    OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
)
from tracer.utils.observability_provider import create_observability_provider
from tracer.utils.otel import ResourceLimitError
from tracer.utils.replay_session import link_agent_to_replay_session

logger = structlog.get_logger(__name__)


def soft_delete_agent_definition_and_versions(agent):
    deleted_at = timezone.now()
    AgentVersion.objects.filter(agent_definition=agent).update(
        deleted=True,
        deleted_at=deleted_at,
    )
    agent.deleted = True
    agent.deleted_at = deleted_at
    agent.save(update_fields=["deleted", "deleted_at", "updated_at"])


class AgentDefinitionView(APIView):
    """
    API View to list agent definitions for an organization with pagination and search,
    and to bulk-delete agent definitions.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=AgentDefinitionFilterSerializer,
        responses={
            200: AgentDefinitionListResponseSerializer(many=True),
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
        framework_query_params=("page", "limit"),
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
                return self._gm.not_found("Organization not found for the user.")

            validated = request.validated_query_data
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
            return self._gm.internal_server_error_response(
                f"Failed to retrieve agent definitions: {str(e)}"
            )

    @validated_request(
        request_serializer=AgentDefinitionBulkDeleteRequestSerializer,
        responses={
            200: AgentDefinitionBulkDeleteResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def delete(self, request):
        """
        Bulk soft-delete agent definitions.
        """
        try:
            agent_ids = request.validated_data["agent_ids"]

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
            return self._gm.internal_server_error_response(
                f"Failed to delete agents: {str(e)}"
            )


class CreateAgentDefinitionView(APIView):
    """
    API View to create a new agent definition.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=AgentDefinitionCreateRequestSerializer,
        responses={
            201: AgentDefinitionCreateResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        """
        Create a new agent definition with its first version.
        """
        try:
            validated = request.validated_data
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

            # Create the first version first so ProviderCredentials can link to it.
            version = agent.create_version(
                description=description,
                commit_message=commit_message,
                status=AgentVersion.StatusChoices.ACTIVE,
            )

            # Route livekit/provider credentials to ProviderCredentials table,
            # now linked to the version instead of the agent definition.
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
                provider_was_provided="provider" in request.data,
            )
            sync_provider_credentials(version, creds_input)

            # Re-snapshot now that ProviderCredentials exist (LiveKit fields)
            version.configuration_snapshot = version.create_snapshot(
                commit_message=version.commit_message or ""
            )
            version.save(update_fields=["configuration_snapshot"])

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
        except ResourceLimitError:
            return self._gm.bad_request("PROJECT CREATION LIMIT REACHED")
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Failed to create agent definition: {str(e)}"
            )


class AgentDefinitionDetailView(APIView):
    """
    API View to retrieve a specific agent definition with version history.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: AgentDefinitionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, *args, **kwargs):
        """
        Get details of a specific agent definition with version information.
        """
        try:
            agent = AgentDefinition.objects.get(
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
            return self._gm.not_found("Agent definition not found")
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Failed to retrieve agent definition: {str(e)}"
            )


class AgentDefinitionOperationsViewSet(BaseModelViewSetMixin, ModelViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = AgentDefinitionResponseSerializer

    def get_serializer_class(self):
        if getattr(self, "action", None) in {"create", "update", "partial_update"}:
            return AgentDefinitionSerializer
        return AgentDefinitionResponseSerializer

    def get_queryset(self):
        return super().get_queryset()

    @swagger_auto_schema(
        responses={
            400: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            400: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None)
            or getattr(self.request.user, "workspace", None),
        )

    @swagger_auto_schema(
        responses={
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        soft_delete_agent_definition_and_versions(instance)

    @validated_request(
        request_serializer=FetchAssistantRequestSerializer,
        responses={
            200: FetchAssistantResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            403: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"])
    def fetch_assistant_from_provider(self, request):
        """
        Fetches the details of agent from the provider and sends them to the client.
        It DOES NOT create a new version.
        """

        try:
            validated = request.validated_data

            api_key = validated["api_key"]
            provider = validated["provider"]
            assistant_id = validated["assistant_id"]
            prompt = ""
            name = ""

            if is_masked(api_key):
                api_key = resolve_stored_api_key(
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    workspace=getattr(request, "workspace", None),
                    agent_id=validated.get("agent_id"),
                    assistant_id=assistant_id,
                    masked_value=api_key,
                )
                if not api_key:
                    msg = "Cannot sync with a masked API key. Please paste the actual key."
                    return self._gm.bad_request(msg)

            if provider == ProviderChoices.VAPI:
                from tfc.ee_gating import EEFeature, check_ee_feature

                org = (
                    getattr(request, "organization", None) or request.user.organization
                )
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

            elif provider == ProviderChoices.BLAND:
                # Bland's "assistant" is a Conversational Pathway, fetched by id.
                resp = requests.get(
                    f"{ObservabilityRoutes.BLAND_PATHWAY_URL.value}/{assistant_id}",
                    headers={"authorization": api_key},
                    timeout=OBSERVABILITY_VERIFY_TIMEOUT_SECONDS,
                )
                resp.raise_for_status()
                pathway = resp.json()
                name = pathway.get("name") or ""
                # Pathways are node graphs, not a single prompt — surface the
                # description and each node's prompt so the synced agent carries
                # the pathway's behaviour.
                node_texts = [
                    (node.get("data") or {}).get("prompt")
                    or (node.get("data") or {}).get("text")
                    for node in pathway.get("nodes", [])
                ]
                prompt = "\n\n".join(
                    text
                    for text in [pathway.get("description"), *node_texts]
                    if text
                )

            response_data = {
                "assistant_id": assistant_id,
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
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=AgentDefinitionEditRequestSerializer,
        responses={
            200: AgentDefinitionEditResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def put(self, request, agent_id, *args, **kwargs):
        """
        Update an existing agent definition.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Update agent fields directly from validated data. NOTE:
            # ``livekit_*`` fields are NOT model columns on AgentDefinition;
            # they live on the related ProviderCredentials row and are
            # routed below via ``_sync_provider_credentials``.
            validated = request.validated_data
            incoming_api_key = validated.get("api_key")
            preserve_existing_api_key = incoming_api_key is not None and is_masked(
                incoming_api_key
            )
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
                if field == "api_key" and preserve_existing_api_key:
                    continue
                if field in validated:
                    setattr(agent, field, validated[field])
            if "knowledge_base" in validated:
                agent.knowledge_base_id = validated["knowledge_base"]
            agent.save()

            # Route livekit_* fields to ProviderCredentials so they
            # actually persist (setattr on the model is a no-op for these
            # since they aren't real columns).
            if preserve_existing_api_key:
                version = agent.active_version or agent.latest_version
                existing_api_key = (
                    resolve_api_key_for_version(version) if version else None
                )
            else:
                existing_api_key = None
            version = agent.active_version or agent.latest_version

            creds_input = ProviderCredentialsInput(
                provider=validated.get("provider") or agent.provider or "",
                api_key=existing_api_key
                if preserve_existing_api_key
                else validated.get("api_key"),
                assistant_id=validated.get("assistant_id"),
                livekit_url=validated.get("livekit_url"),
                livekit_api_key=validated.get("livekit_api_key"),
                livekit_api_secret=validated.get("livekit_api_secret"),
                livekit_agent_name=validated.get("livekit_agent_name"),
                livekit_config_json=validated.get("livekit_config_json"),
                livekit_max_concurrency=validated.get("livekit_max_concurrency"),
                provider_was_provided="provider" in request.data,
            )
            if version:
                sync_provider_credentials(version, creds_input)
            updated_agent = agent

            response_data = {
                "message": "Agent definition updated successfully",
                "agent": AgentDefinitionResponseSerializer(updated_agent).data,
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except AgentDefinition.DoesNotExist:
            return self._gm.not_found("Agent definition not found")
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Failed to update agent definition: {str(e)}"
            )


class DeleteAgentDefinitionView(APIView):
    """
    API View to delete an agent definition (soft delete).
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: AgentDefinitionDeleteResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
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

            soft_delete_agent_definition_and_versions(agent)

            response_data = {"message": "Agent definition deleted successfully"}
            return Response(
                AgentDefinitionDeleteResponseSerializer(response_data).data,
                status=status.HTTP_200_OK,
            )

        except AgentDefinition.DoesNotExist:
            return self._gm.not_found("Agent definition not found")
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Failed to delete agent definition: {str(e)}"
            )
