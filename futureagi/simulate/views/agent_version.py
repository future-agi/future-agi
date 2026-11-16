from django.conf import settings
from django.db import transaction
from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.develop_dataset import KnowledgeBaseFile
from simulate.models import AgentDefinition, AgentVersion, CallExecution
from simulate.models.agent_definition import ProviderCredentials
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
    accessible_credential_agents,
    is_masked,
    provider_family,
    provider_for_version,
    resolve_api_key_for_version,
    resolve_provider_configuration_for_version,
    source_version_for_agent,
    sync_provider_credentials,
    validate_provider_change,
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
from tracer.services.observability_switch import (
    ObservabilityRefused,
    agent_is_source,
    observability_is_on,
    refusal_body,
    set_agent_observability,
)


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
            workspace = getattr(request, "workspace", None)
            agent = accessible_credential_agents(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                workspace=workspace,
            ).get(id=agent_id)

            validated = request.validated_data
            commit_message = validated.get("commit_message", "")
            observability_enabled = validated.get("observability_enabled")

            incoming_api_key = validated.get("api_key")
            preserve_existing_api_key = incoming_api_key is not None and is_masked(
                incoming_api_key
            )
            # Select and authorize the source before mutating the definition.
            # In particular, a legacy snapshot without provider must still see
            # the pre-write definition, and Bland's VAPI row encoding is not Vapi.
            source_version_id = validated.get("source_version_id")
            source_version = source_version_for_agent(agent, source_version_id)
            source_provider = (
                provider_for_version(source_version)
                if source_version
                else agent.provider
            )
            requested_provider = validated.get("provider") or agent.provider or ""
            provider_changed = provider_family(requested_provider) != provider_family(
                source_provider
            )
            definition_provider_changed = provider_family(
                requested_provider
            ) != provider_family(agent.provider)
            # A source agent's first settings save selects a provider without
            # crossing an existing credential authority. A blank provider alone
            # is not enough: definition, version and legacy credentials close
            # this exception, including rows whose secrets cannot be decrypted.
            initial_source_configuration = (
                provider_changed
                and not source_provider
                and not agent.provider
                and not agent.api_key
                and agent_is_source(agent)
                and not ProviderCredentials.objects.filter(
                    Q(agent_definition=agent) | Q(agent_version__agent_definition=agent)
                ).exists()
            )
            if not (
                initial_source_configuration and not (incoming_api_key or "").strip()
            ):
                validate_provider_change(
                    source_provider=source_provider,
                    provider=requested_provider,
                    api_key=incoming_api_key,
                )
            # Omission keeps the selected source even when it was selected by
            # active/latest rather than an explicit ID. Otherwise sync could
            # adopt an unrelated, unversioned legacy key on the next save.
            # LiveKit uses its own fields below; a fresh replacement must not
            # decrypt the old key merely because generic api_key was omitted.
            carry_api_key = (
                provider_family(requested_provider) != "livekit"
                and not provider_changed
                and (preserve_existing_api_key or not incoming_api_key)
            )
            existing_configuration = None
            existing_api_key = None
            source_credentials = None
            livekit_fields = {
                "livekit_url": "server_url",
                "livekit_api_key": "api_key",
                "livekit_api_secret": "api_secret",
                "livekit_agent_name": "agent_name",
                "livekit_config_json": "config_json",
                "livekit_max_concurrency": "max_concurrency",
            }
            livekit_input = {name: validated.get(name) for name in livekit_fields}
            if source_version is not None and not provider_changed:
                source_credentials = ProviderCredentials.objects.filter(
                    agent_version=source_version
                ).first()
                if carry_api_key:
                    # A displayed mask is not authority: compare it with the
                    # resolved, authorized source, even when preflight is bypassed.
                    if preserve_existing_api_key:
                        from agentcc.services.credential_manager import mask_key

                        resolved_key = resolve_api_key_for_version(source_version)
                        legacy_keep_marker = (
                            source_version_id is None
                            and incoming_api_key in ("********", "****")
                        )
                        if not resolved_key or (
                            not legacy_keep_marker
                            and mask_key(resolved_key) != incoming_api_key
                        ):
                            return _error_response(
                                "API key does not match the selected source version",
                                status.HTTP_400_BAD_REQUEST,
                            )
                    existing_configuration = resolve_provider_configuration_for_version(
                        source_version, for_save=True
                    )
                    existing_api_key = existing_configuration.api_key
                    if (
                        requested_provider == "vapi"
                        and not existing_api_key
                        and not existing_configuration.uses_definition_fallback
                    ):
                        # None is also the for-save value of a valid raw-empty
                        # fallback. Only that source may create another empty
                        # marker: an unusable authoritative source must not be
                        # reopened against the active definition's different key.
                        raise ValidationError(
                            {
                                "api_key": (
                                    "The selected source version has no usable API key. "
                                    "Please provide a new API key."
                                )
                            }
                        )
                if provider_family(requested_provider) == "livekit":
                    livekit_credentials = source_credentials
                    if livekit_credentials is None:
                        livekit_credentials = ProviderCredentials.objects.filter(
                            agent_definition=agent
                        ).first()
                    if (
                        livekit_credentials is not None
                        and livekit_credentials.provider_type == "livekit"
                        and (
                            livekit_credentials.agent_version_id is None
                            or provider_family(
                                provider_for_version(livekit_credentials.agent_version)
                            )
                            == "livekit"
                        )
                    ):
                        for name, column in livekit_fields.items():
                            value = livekit_input[name]
                            keep_secret = name in (
                                "livekit_api_key",
                                "livekit_api_secret",
                            ) and (not value or is_masked(value))
                            if name not in validated or keep_secret:
                                # Copy encrypted values, not decrypted plaintext:
                                # alias saves retain ciphertext as well as settings.
                                livekit_input[name] = getattr(
                                    livekit_credentials, column
                                )

            preserve_selected_row = source_credentials is not None and (
                provider_family(requested_provider) == "livekit"
                or (
                    carry_api_key
                    and bool(source_credentials.api_key)
                    and bool(existing_api_key)
                )
            )

            # Keep the raw-empty Vapi source marker instead of adopting a legacy
            # row. Also prevent adoption of the old provider's row on a switch.
            preserve_empty_vapi_row = (
                requested_provider == "vapi"
                and not provider_changed
                and source_version is not None
                and ProviderCredentials.objects.filter(
                    agent_version=source_version, provider_type="vapi", api_key=""
                ).exists()
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
                "target_speaks_first",
                "model",
                "model_details",
            ]
            changed_fields = []
            for field in update_fields:
                if field == "api_key" and (preserve_existing_api_key or carry_api_key):
                    continue
                if field in validated:
                    setattr(agent, field, validated[field])
                    changed_fields.append(field)
            if (provider_changed or definition_provider_changed) and (
                not incoming_api_key or preserve_existing_api_key
            ):
                agent.api_key = ""
                if "api_key" not in changed_fields:
                    changed_fields.append("api_key")
            if provider_changed:
                if "assistant_id" not in validated:
                    agent.assistant_id = ""
                    changed_fields.append("assistant_id")
            elif (
                existing_configuration is not None
                and (source_version_id is not None or existing_api_key)
                and requested_provider in ("vapi", "bland", "retell")
                and "assistant_id" not in validated
            ):
                agent.assistant_id = existing_configuration.assistant_id or ""
                changed_fields.append("assistant_id")

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
                changed_fields.append("knowledge_base")
            with transaction.atomic():
                if changed_fields:
                    # Only these columns: a full save would write back this
                    # request's stale copy of the observability link.
                    agent.save(update_fields=[*changed_fields, "updated_at"])

                version = agent.create_version(
                    description=agent.description,
                    commit_message=commit_message,
                    status=AgentVersion.StatusChoices.ACTIVE,
                )
                version.activate()

                assistant_input = (
                    {"assistant_id": validated["assistant_id"]}
                    if "assistant_id" in validated
                    else {}
                )
                if (
                    not assistant_input
                    and existing_api_key
                    and requested_provider in ("vapi", "bland", "retell")
                ):
                    # Carry the selected key's identity, not the mutable
                    # definition's assistant, into a retained-key version save.
                    assistant_input["assistant_id"] = (
                        existing_configuration.assistant_id
                    )
                creds_input = ProviderCredentialsInput(
                    provider=validated.get("provider") or agent.provider or "",
                    api_key=existing_api_key
                    if carry_api_key or preserve_existing_api_key
                    else validated.get("api_key"),
                    **assistant_input,
                    **livekit_input,
                    provider_was_provided="provider" in request.data,
                )
                # The page always sends the provider and LiveKit settings, so a code
                # agent saved with every credential blank would get an empty row.
                # A request with no credential fields writes no row, so the agent is
                # only read when fields were sent.
                sent_fields = (
                    creds_input.provider_was_provided
                    or "assistant_id" in creds_input.model_fields_set
                    or any(
                        value is not None
                        for value in (
                            creds_input.api_key,
                            creds_input.livekit_url,
                            creds_input.livekit_api_key,
                            creds_input.livekit_api_secret,
                            creds_input.livekit_agent_name,
                            creds_input.livekit_config_json,
                            creds_input.livekit_max_concurrency,
                        )
                    )
                )
                # Only the fields the provider's row would save count, so a LiveKit
                # agent's leftover key or assistant id does not make a blank row.
                if (creds_input.provider or "").strip() in (
                    "livekit",
                    "livekit_bridge",
                ):
                    saved_values = (
                        creds_input.livekit_url,
                        creds_input.livekit_api_key,
                        creds_input.livekit_api_secret,
                        creds_input.livekit_agent_name,
                    )
                    saved_config = creds_input.livekit_config_json
                else:
                    saved_values = (creds_input.api_key, creds_input.assistant_id)
                    saved_config = None
                has_credential_value = any(
                    (value or "").strip() for value in saved_values
                )
                # Max Concurrency and Room Config are kept only on this row, and the
                # page sends the default when nothing was changed.
                has_setting_value = bool(saved_config) or (
                    creds_input.livekit_max_concurrency is not None
                    and creds_input.livekit_max_concurrency
                    != settings.DEFAULT_LIVEKIT_MAX_CONCURRENCY
                )
                skip_blank_source_credentials = (
                    sent_fields
                    and not has_credential_value
                    and not has_setting_value
                    and agent_is_source(agent)
                )
                # Do not pre-create the row the blank-source guard would skip.
                # Selected keyed rows, provider transitions and raw-empty Vapi
                # markers must prevent adoption of unrelated legacy credentials.
                if (
                    preserve_selected_row
                    or preserve_empty_vapi_row
                    or (
                        provider_changed
                        and not (
                            initial_source_configuration
                            and skip_blank_source_credentials
                        )
                    )
                ):
                    if requested_provider in ("livekit", "livekit_bridge"):
                        storage_provider = ProviderCredentials.ProviderType.LIVEKIT
                    elif requested_provider == "retell":
                        storage_provider = ProviderCredentials.ProviderType.RETELL
                    else:
                        storage_provider = ProviderCredentials.ProviderType.VAPI
                    ProviderCredentials.objects.create(
                        agent_version=version,
                        provider_type=storage_provider,
                    )
                if not skip_blank_source_credentials:
                    sync_provider_credentials(version, creds_input)

                if observability_enabled is None:
                    # An absent field leaves a code agent's switch as it is.
                    observability_enabled = observability_is_on(
                        agent
                    ) and agent_is_source(agent)
                set_agent_observability(
                    agent_definition=agent,
                    enabled=observability_enabled,
                    user_id=str(request.user.id),
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    workspace=agent.workspace,
                )

                # Sync updates a separately queried credential instance. Both
                # precreation and legacy adoption can cache an older relation on
                # this object, so always reload before snapshotting/serialization.
                version.refresh_from_db()
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
        except AgentVersion.DoesNotExist:
            return _error_response(
                "Source version not found for this agent", status.HTTP_400_BAD_REQUEST
            )
        except ObservabilityRefused as error:
            return Response(refusal_body(error), status=status.HTTP_400_BAD_REQUEST)
        except ValidationError:
            raise
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
            # The call-log grid sends page_size, as it does to the trace lists;
            # limit is still read when that is what a caller sends.
            if "page_size" in request.query_params:
                paginator.page_size_query_param = "page_size"
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
