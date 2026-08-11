from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.develop_dataset import KnowledgeBaseFile
from simulate.models import AgentDefinition, AgentVersion, CallExecution
from simulate.serializers.requests.agent_version import (
    AgentVersionCreateRequestSerializer,
)
from simulate.serializers.response.agent_definition import (
    AgentDefinitionResponseSerializer,
)
from simulate.serializers.response.agent_version import (
    AgentVersionActivateResponseSerializer,
    AgentVersionCreateResponseSerializer,
    AgentVersionDeleteResponseSerializer,
    AgentVersionListResponseSerializer,
    AgentVersionResponseSerializer,
    AgentVersionRestoreResponseSerializer,
)
from simulate.serializers.response.run_test_evals import (
    EvalErrorResponseSerializer,
    EvalSummaryResponseSerializer,
)
from simulate.serializers.test_execution import CallExecutionSerializer
from simulate.services.agent_definition import (
    is_masked,
    resolve_api_key_for_version,
    sync_provider_credentials,
)
from simulate.services.types.agent_definition import ProviderCredentialsInput
from simulate.utils.eval_summary import (
    _build_template_statistics,
    _calculate_final_template_summaries,
    _get_completed_call_executions_for_agent_version,
    _get_eval_config_for_agent_version,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import (
    ApiErrorWithDetailsResponseSerializer,
    EmptyRequestSerializer,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tracer.utils.observability_provider import create_observability_provider


def _error_response(message, status_code):
    return Response(
        build_error_envelope(message, status_code=status_code),
        status=status_code,
    )


class AgentVersionListView(APIView):
    """
    API View to list all versions of an agent definition.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: AgentVersionListResponseSerializer(many=True),
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, *args, **kwargs):
        """
        Get all versions of a specific agent definition.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            versions = agent.get_version_history()

            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(versions, request)

            serializer = AgentVersionListResponseSerializer(result_page, many=True)
            return paginator.get_paginated_response(serializer.data)

        except AgentDefinition.DoesNotExist:
            return _error_response(
                "Agent definition not found", status.HTTP_404_NOT_FOUND
            )
        except NotFound:
            raise
        except Exception as e:
            return _error_response(
                f"Failed to retrieve agent versions: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class CreateAgentVersionView(APIView):
    """
    API View to create a new version of an agent definition.
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=AgentVersionCreateRequestSerializer,
        responses={
            201: AgentVersionCreateResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, agent_id, *args, **kwargs):
        """
        Create a new version of an agent definition.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
            )

            validated = request.validated_data
            commit_message = validated.get("commit_message", "")
            observability_enabled = validated.get("observability_enabled", False)

            incoming_api_key = validated.get("api_key")
            preserve_existing_api_key = incoming_api_key is not None and is_masked(
                incoming_api_key
            )

            # Update agent definition fields directly from validated data
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
            ]
            changed = False
            for field in update_fields:
                if field == "api_key" and preserve_existing_api_key:
                    continue
                if field in validated:
                    setattr(agent, field, validated[field])
                    changed = True

            if "knowledge_base" in validated:
                kb_id = validated["knowledge_base"]
                if kb_id is not None:
                    # Validate knowledge base belongs to organization
                    organization = (
                        getattr(request, "organization", None)
                        or request.user.organization
                    )
                    if not KnowledgeBaseFile.objects.filter(
                        id=kb_id, organization=organization
                    ).exists():
                        return Response(
                            build_error_envelope(
                                "Invalid data for agent update",
                                status_code=status.HTTP_400_BAD_REQUEST,
                                details={
                                    "knowledge_base": [
                                        "Knowledge base not found in your organization."
                                    ]
                                },
                            ),
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                agent.knowledge_base_id = kb_id
                changed = True
            if changed:
                agent.save()
            provider = agent.observability_provider

            if provider:
                is_project_deleted = provider.project.deleted
                if is_project_deleted:
                    agent.observability_provider = None
                    agent.save()
                else:
                    provider.enabled = observability_enabled
                    provider.save()
            else:
                if observability_enabled:
                    provider = create_observability_provider(
                        enabled=True,
                        user_id=str(request.user.id),
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        workspace=getattr(request.user, "workspace", None),
                        project_name=agent.agent_name,
                        provider=agent.provider,
                    )
                    agent.observability_provider = provider
                    agent.save()

            # Resolve the API key *before* creating the new version so we
            # read from the active version rather than the brand-new one.
            if preserve_existing_api_key:
                active_version = agent.active_version or agent.latest_version
                existing_api_key = (
                    resolve_api_key_for_version(active_version) if active_version else None
                )
            else:
                existing_api_key = None

            version = agent.create_version(
                description=agent.description,
                commit_message=commit_message,
                status=AgentVersion.StatusChoices.ACTIVE,
            )
            version.activate()

            creds_input = ProviderCredentialsInput(
                provider=validated.get("provider") or agent.provider or "",
                api_key=existing_api_key if preserve_existing_api_key else validated.get("api_key"),
                assistant_id=validated.get("assistant_id"),
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

            response_data = {
                "message": "Agent version created successfully",
                "version": AgentVersionResponseSerializer(version).data,
            }
            return Response(response_data, status=status.HTTP_201_CREATED)

        except AgentDefinition.DoesNotExist:
            return _error_response(
                "Agent definition not found", status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return _error_response(
                f"Failed to create agent version: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AgentVersionDetailView(APIView):
    """
    API View to retrieve a specific agent version.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: AgentVersionResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        """
        Get details of a specific agent version.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            version = AgentVersion.objects.get(id=version_id, agent_definition=agent)

            serializer = AgentVersionResponseSerializer(version)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except AgentDefinition.DoesNotExist:
            return _error_response(
                "Agent definition not found", status.HTTP_404_NOT_FOUND
            )
        except AgentVersion.DoesNotExist:
            return _error_response("Agent version not found", status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return _error_response(
                f"Failed to retrieve agent version: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ActivateAgentVersionView(APIView):
    """
    API View to activate a specific agent version.
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: AgentVersionActivateResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, agent_id, version_id, *args, **kwargs):
        """
        Activate a specific agent version.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            version = AgentVersion.objects.get(id=version_id, agent_definition=agent)
            version.activate()

            response_data = {
                "message": "Agent version activated successfully",
                "version": AgentVersionResponseSerializer(version).data,
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except AgentDefinition.DoesNotExist:
            return _error_response(
                "Agent definition not found", status.HTTP_404_NOT_FOUND
            )
        except AgentVersion.DoesNotExist:
            return _error_response("Agent version not found", status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return _error_response(
                f"Failed to activate agent version: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DeleteAgentVersionView(APIView):
    """
    API View to delete an agent version (soft delete).
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: AgentVersionDeleteResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def delete(self, request, agent_id, version_id, *args, **kwargs):
        """
        Soft delete an agent version.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            version = AgentVersion.objects.get(id=version_id, agent_definition=agent)

            if version.is_active:
                active_versions = AgentVersion.objects.filter(
                    agent_definition=agent, status="active"
                ).exclude(id=version_id)

                if not active_versions.exists():
                    return _error_response(
                        "Cannot delete the only active version. Please activate another version first.",
                        status.HTTP_400_BAD_REQUEST,
                    )

            version.delete()

            response_data = {"message": "Agent version deleted successfully"}
            return Response(
                AgentVersionDeleteResponseSerializer(response_data).data,
                status=status.HTTP_200_OK,
            )

        except AgentDefinition.DoesNotExist:
            return _error_response(
                "Agent definition not found", status.HTTP_404_NOT_FOUND
            )
        except AgentVersion.DoesNotExist:
            return _error_response("Agent version not found", status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return _error_response(
                f"Failed to delete agent version: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class RestoreAgentVersionView(APIView):
    """
    API View to restore an agent definition from a specific version.
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: AgentVersionRestoreResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, agent_id, version_id, *args, **kwargs):
        """
        Restore agent definition from a specific version.
        """
        try:
            agent = AgentDefinition.objects.get(
                id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            version = AgentVersion.objects.get(id=version_id, agent_definition=agent)

            restored_agent = version.restore_from_snapshot()

            response_data = {
                "message": "Agent definition restored successfully from version",
                "agent": AgentDefinitionResponseSerializer(restored_agent).data,
                "version": AgentVersionResponseSerializer(version).data,
            }
            return Response(response_data, status=status.HTTP_200_OK)

        except AgentDefinition.DoesNotExist:
            return _error_response(
                "Agent definition not found", status.HTTP_404_NOT_FOUND
            )
        except AgentVersion.DoesNotExist:
            return _error_response("Agent version not found", status.HTTP_404_NOT_FOUND)
        except ValueError as e:
            return _error_response(str(e), status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return _error_response(
                f"Failed to restore agent version: {str(e)}",
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AgentVersionEvalSummaryView(APIView):
    """
    API View to get the eval summary of an agent version.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: EvalSummaryResponseSerializer,
            404: EvalErrorResponseSerializer,
            500: EvalErrorResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        """
        Get the eval summary of an agent version.
        """
        try:
            version = AgentVersion.objects.get(
                id=version_id,
                agent_definition__id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            eval_configs = _get_eval_config_for_agent_version(version)

            if not eval_configs:
                return self._gm.success_response([])

            call_executions = _get_completed_call_executions_for_agent_version(version)
            template_stats = _build_template_statistics(eval_configs, call_executions)
            final_data = _calculate_final_template_summaries(template_stats)

            return self._gm.success_response(final_data)

        except AgentVersion.DoesNotExist:
            return self._gm.not_found("Agent version not found.")
        except Exception:
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_EVAL_SUMMARY")
            )


class AgentVersionCallExecutionView(APIView):
    """
    API View to get the call executions of an agent version.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: CallExecutionSerializer(many=True),
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        },
    )
    def get(self, request, agent_id, version_id, *args, **kwargs):
        """
        Get the call executions of an agent version.
        """
        try:
            version = AgentVersion.objects.get(
                id=version_id,
                agent_definition__id=agent_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            call_executions = CallExecution.objects.filter(
                agent_version=version, status="completed", eval_outputs__isnull=False
            ).exclude(eval_outputs={})

            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(call_executions, request)
            serializer = CallExecutionSerializer(result_page, many=True)

            return paginator.get_paginated_response(serializer.data)

        except AgentVersion.DoesNotExist:
            return self._gm.not_found("Agent version not found.")
        except NotFound:
            raise
        except Exception:
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_CALL_EXECUTIONS")
            )
