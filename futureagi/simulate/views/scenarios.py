import json
import re
import traceback
import uuid

from django.db import close_old_connections, models, transaction
from django.db.models import Count, Max, OuterRef, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.user import User

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
        EnhancedScenariosAgent,
    )
    from ee.agenthub.scenario_graph.graph_generator import (
        ConversationGraphGenerator,
    )
    from ee.agenthub.scenario_graph.persona_configurator import (
        PersonaConfigurator,
    )
except ImportError:
    EnhancedScenariosAgent = _ee_stub("EnhancedScenariosAgent")
    ConversationGraphGenerator = _ee_stub("ConversationGraphGenerator")
    PersonaConfigurator = _ee_stub("PersonaConfigurator")
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.develop_dataset import ColumnSerializer
try:
    from ee.voice.constants.voice_mapper import get_personas_by_language
except ImportError:
    get_personas_by_language = None
from simulate.models import AgentDefinition, AgentVersion, Persona, Scenarios
from simulate.models.scenario_graph import ScenarioGraph
from simulate.models.simulator_agent import SimulatorAgent
from drf_yasg.utils import swagger_auto_schema
from simulate.serializers.requests.scenarios import (
    ScenarioAddColumnsRequestSerializer,
    ScenarioAddRowsRequestSerializer,
    ScenarioCreateRequestSerializer,
    ScenarioEditPromptsRequestSerializer,
    ScenarioEditRequestSerializer,
    ScenarioFilterSerializer,
    ScenarioMultiDatasetFilterSerializer,
)
from simulate.serializers.response.scenarios import (
    ScenarioAddColumnsResponseSerializer,
    ScenarioAddRowsResponseSerializer,
    ScenarioCreateResponseSerializer,
    ScenarioDeleteResponseSerializer,
    ScenarioDetailResponseSerializer,
    ScenarioEditResponseSerializer,
    ScenarioErrorResponseSerializer,
    ScenarioListResponseSerializer,
    ScenarioMultiDatasetResponseSerializer,
    ScenarioPromptsUpdateResponseSerializer,
    ScenarioResponseSerializer,
)
from simulate.utils.test_execution_utils import generate_simulator_agent_prompt
from simulate.views import agent_definition
from tfc.middleware.workspace_context import get_current_organization
from tfc.temporal.simulate import (
    start_add_columns_workflow_sync,
    start_add_scenario_rows_workflow_sync,
    start_create_dataset_scenario_workflow_sync,
    start_create_graph_scenario_workflow_sync,
    start_create_script_scenario_workflow_sync,
    start_scenario_generation_workflow_sync,
)
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


def convert_personas_to_property_list(persona_ids):
    """
    Convert persona IDs to property_list format expected by EnhancedScenariosAgent.

    Args:
        persona_ids: List of persona UUIDs

    Returns:
        List of property dictionaries matching the SDA format

    Note: All persona fields are now stored as lists in the model, so we use them directly.
    """
    if not persona_ids:
        return None

    property_list = []
    personas = Persona.no_workspace_objects.filter(id__in=persona_ids, deleted=False)

    for persona in personas:
        # Helper to safely convert fields
        def safe_json_field_to_list(value, default):
            """
            Safely convert a field value to a list.
            """
            if not value or value == "":
                return default
            if isinstance(value, list):
                return value
            elif isinstance(value, str):
                return [value]
            return default

        prop_dict = {
            "min_length": 50,
            "max_length": 400,
        }

        # Determine mode from persona.simulation_type if available, otherwise default to "voice"
        mode = persona.simulation_type
        if not mode:
            mode = "voice"

        target_props = PersonaConfigurator.get_property_dict(mode)

        # Dynamically populate properties based on PersonaConfigurator
        for key, default_values in target_props.items():
            model_key = key
            if key == "profession":
                model_key = "occupation"
            elif key == "language":
                model_key = "languages"

            if hasattr(persona, model_key):
                val = getattr(persona, model_key)
                # Ensure we use the value if it's explicitly set
                prop_dict[key] = safe_json_field_to_list(val, default_values)
            else:
                prop_dict[key] = default_values

        prop_dict["name"] = target_props["name"]
        # Add metadata (custom_properties) if present
        if persona.metadata:
            prop_dict["metadata"] = persona.metadata

        # Add additional_instruction if present
        if persona.additional_instruction:
            prop_dict["additional_instruction"] = persona.additional_instruction

        property_list.append(prop_dict)

    return property_list if property_list else None


class ScenariosListView(APIView):
    """
    API View to list scenarios for an organization with pagination and search
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def dispatch(self, request, *args, **kwargs):
        # Initialize request properly
        self.args = args
        self.kwargs = kwargs
        request = self.initialize_request(request, *args, **kwargs)
        self.request = request
        self.headers = self.default_response_headers

        action = kwargs.get("action")

        try:
            self.initial(request, *args, **kwargs)

            action_methods = {
                "multi-dataset": self.get_multi_datasets_column_configs,
            }

            if action in action_methods:
                response = action_methods[action](request, *args, **kwargs)
            else:
                handler = getattr(
                    self, request.method.lower(), self.http_method_not_allowed
                )
                response = handler(request, *args, **kwargs)
        except Exception as exc:
            response = self.handle_exception(exc)

        # CRITICAL: This line fixes the renderer error
        self.response = self.finalize_response(request, response, *args, **kwargs)
        return self.response

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="List scenarios",
        operation_description="Returns a paginated list of scenarios for the user's organization.",
        query_serializer=ScenarioFilterSerializer,
        responses={
            200: ScenarioListResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def get(self, request, *args, **kwargs):
        """
        Get paginated list of scenarios for the user's organization
        Query Parameters:
        - search: search string to filter scenarios by name and source
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        """
        try:
            # Get the organization of the logged-in user
            user_organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            if not user_organization:
                return Response(
                    {"error": "Organization not found for the user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Validate and parse query params
            filter_ser = ScenarioFilterSerializer(data=request.query_params)
            filter_ser.is_valid()  # defaults applied even if invalid; raw fallback kept below
            search_query = request.query_params.get("search", "").strip()
            agent_definition_id = request.query_params.get("agent_definition_id", None)
            agent_type = request.query_params.get("agent_type", None)

            # Build base queryset with optimized joins
            base_queryset = Scenarios.objects.filter(
                organization=user_organization,
                deleted=False,
            )

            if agent_definition_id:
                base_queryset = base_queryset.filter(
                    agent_definition_id=agent_definition_id
                )

            # Annotate with dataset row count (avoids N+1 COUNT queries)
            row_count_subquery = (
                Row.objects.filter(dataset=OuterRef("dataset"), deleted=False)
                .values("dataset")
                .annotate(cnt=Count("id"))
                .values("cnt")
            )

            scenarios = base_queryset.select_related(
                "dataset",
                "simulator_agent",
                "prompt_template",
                "prompt_version",
                "agent_definition",
            ).annotate(_dataset_row_count=Subquery(row_count_subquery))

            # Apply search filter if search query is provided
            if search_query:
                # Create case-insensitive regex pattern for strong search
                pattern = rf"(?i){re.escape(search_query)}"
                scenarios = scenarios.filter(
                    models.Q(name__regex=pattern)
                    | models.Q(source__regex=pattern)
                    | models.Q(scenario_type__regex=pattern)
                )

            if agent_type is not None:
                subquery = Q(agent_definition__agent_type=agent_type)
                if agent_type == AgentDefinition.AgentTypeChoices.TEXT:
                    subquery |= Q(source_type=Scenarios.SourceTypes.PROMPT)

                scenarios = scenarios.filter(subquery)

            # Order by creation date (newest first)
            scenarios = scenarios.order_by("-created_at")

            # Apply pagination
            paginator = ExtendedPageNumberPagination()
            result_page = paginator.paginate_queryset(scenarios, request)

            # Serialize the data
            serializer = ScenarioResponseSerializer(result_page, many=True)

            # Return paginated response
            return paginator.get_paginated_response(serializer.data)

        except NotFound:
            raise
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve scenarios: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Get multi-dataset column configs",
        operation_description="Returns column configurations for multiple scenarios.",
        query_serializer=ScenarioMultiDatasetFilterSerializer,
        responses={
            200: ScenarioMultiDatasetResponseSerializer,
            400: ScenarioErrorResponseSerializer,
        },
    )
    def get_multi_datasets_column_configs(self, request, *args, **kwargs):
        filter_ser = ScenarioMultiDatasetFilterSerializer(data=request.query_params)
        if not filter_ser.is_valid():
            return self._gm.bad_request(filter_ser.errors)
        scenario_ids = filter_ser.validated_data["scenarios"]

        try:
            # Get all unique dataset IDs from the filtered scenarios
            scenarios = Scenarios.objects.filter(id__in=scenario_ids, deleted=False)

            return Response(
                ScenarioMultiDatasetResponseSerializer({"column_configs": scenarios}).data,
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return self._gm.bad_request(f"Error fetching column configs: {str(e)}")


class CreateScenarioView(APIView):
    """
    API View to create a new scenario by copying a dataset
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Create scenario",
        operation_description="Creates a new scenario (dataset, script, or graph kind). Returns 202 with processing status.",
        request_body=ScenarioCreateRequestSerializer,
        responses={
            202: ScenarioCreateResponseSerializer,
            400: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        """
        Create a new scenario by copying the specified dataset
        """
        try:
            from tfc.ee_gating import EEFeature, check_ee_feature

            org = getattr(request, "organization", None) or request.user.organization
            check_ee_feature(EEFeature.SYNTHETIC_DATA, org_id=str(org.id))

            # Phase 0: all validation (including 0.1.1–0.1.3) now lives in the serializer
            serializer = ScenarioCreateRequestSerializer(
                data=request.data, context={"request": request}
            )

            if not serializer.is_valid():
                return self.gm.bad_request(
                    {"error": "Invalid data", "details": serializer.errors}
                )

            validated_data = serializer.validated_data
            scenario_kind = validated_data.get("kind", Scenarios.ScenarioTypes.DATASET)

            # Create a temporary scenario record with PROCESSING status
            temp_scenario = self._create_temp_scenario(request, validated_data)

            # Run scenario creation in background using Temporal workflows
            if scenario_kind == "dataset":
                start_create_dataset_scenario_workflow_sync(
                    user_id=request.user.id,
                    validated_data=validated_data,
                    scenario_id=str(temp_scenario.id),
                )
            elif scenario_kind == "script":
                start_create_script_scenario_workflow_sync(
                    validated_data=validated_data,
                    scenario_id=str(temp_scenario.id),
                )
            elif scenario_kind == "graph":
                start_create_graph_scenario_workflow_sync(
                    validated_data=validated_data,
                    scenario_id=str(temp_scenario.id),
                )
            else:
                # Update scenario status to failed for unsupported type
                temp_scenario.status = StatusType.FAILED.value
                temp_scenario.save()
                return self.gm.bad_request(
                    f"Unsupported scenario kind: {scenario_kind}"
                )

            # Return immediate response with processing status
            return Response(
                ScenarioCreateResponseSerializer({
                    "message": f"{scenario_kind.title()} scenario creation started",
                    "scenario": temp_scenario,
                    "status": "processing",
                }).data,
                status=status.HTTP_202_ACCEPTED,
            )

        except Exception as e:
            traceback.print_exc()
            return Response(
                {"error": f"Failed to create scenario: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _create_temp_scenario(self, request, validated_data):
        """Create a temporary scenario record with PROCESSING status"""
        try:
            source_type = validated_data.get("source_type", "agent_definition")

            # Create simulator agent (only for agent_definition source type)
            simulator_agent = None
            agent_definition = None
            prompt_template = None
            prompt_version = None

            if source_type == "prompt":
                from model_hub.models.run_prompt import PromptTemplate, PromptVersion

                pt_filters = {
                    "id": validated_data.get("prompt_template_id"),
                    "deleted": False,
                    "organization": getattr(request, "organization", None)
                    or request.user.organization,
                }
                if hasattr(request.user, "workspace") and request.user.workspace:
                    pt_filters["workspace"] = request.user.workspace
                prompt_template = PromptTemplate.objects.get(**pt_filters)

                pv_filters = {
                    "id": validated_data.get("prompt_version_id"),
                    "deleted": False,
                }
                prompt_version = PromptVersion.objects.get(**pv_filters)
                # Create simulator agent with generic prompt for prompt-based scenarios
                agent_prompt = generate_simulator_agent_prompt(agent_definition=None)
                simulator_agent = SimulatorAgent.objects.create(
                    name=validated_data.get("name", "Default Agent"),
                    prompt=agent_prompt,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    workspace=(
                        request.user.workspace
                        if hasattr(request.user, "workspace")
                        else None
                    ),
                )
            else:
                simulator_agent = self._create_simulator_agent(request, validated_data)
                agent_definition = AgentDefinition.objects.get(
                    id=validated_data.get("agent_definition_id")
                )

            # Create temporary scenario with PROCESSING status
            scenario_kind = validated_data.get("kind", "dataset")
            scenario_type_mapping = {
                "dataset": Scenarios.ScenarioTypes.DATASET,
                "script": Scenarios.ScenarioTypes.SCRIPT,
                "graph": Scenarios.ScenarioTypes.GRAPH,
            }

            # Store persona_ids in metadata for future use
            metadata = {}
            persona_ids = validated_data.get("personas", [])
            if persona_ids:
                metadata["persona_ids"] = [str(pid) for pid in persona_ids]

            # Store agent_definition_version_id and custom_instruction in metadata
            agent_definition_version_id = validated_data.get(
                "agent_definition_version_id"
            )
            if agent_definition_version_id:
                metadata["agent_definition_version_id"] = str(
                    agent_definition_version_id
                )

            custom_instruction = validated_data.get("custom_instruction")
            if custom_instruction:
                metadata["custom_instruction"] = custom_instruction

            scenario = Scenarios.objects.create(
                name=validated_data["name"],
                description=validated_data.get("description", ""),
                source="Processing...",
                scenario_type=scenario_type_mapping.get(
                    scenario_kind, Scenarios.ScenarioTypes.DATASET
                ),
                source_type=source_type,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                workspace=(
                    request.user.workspace
                    if hasattr(request.user, "workspace")
                    else None
                ),
                dataset=None,
                simulator_agent=simulator_agent,
                status=StatusType.PROCESSING.value,
                agent_definition=agent_definition,
                prompt_template=prompt_template,
                prompt_version=prompt_version,
                metadata=metadata,
            )
            return scenario
        except Exception as e:
            raise Exception(f"Failed to create temporary scenario: {str(e)}") from e

    def _create_simulator_agent(self, request, validated_data):
        """Create a simulator agent from validated data"""
        try:
            agent_definition = AgentDefinition.objects.get(
                id=validated_data.get("agent_definition_id")
            )
            agent_version = None
            agent_definition_version_id = validated_data.get(
                "agent_definition_version_id"
            )
            if agent_definition_version_id:
                try:
                    agent_version = AgentVersion.objects.get(
                        id=agent_definition_version_id,
                        agent_definition=agent_definition,
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        deleted=False,
                    )
                except AgentVersion.DoesNotExist:
                    agent_version = None

            agent_prompt = generate_simulator_agent_prompt(
                agent_version=agent_version, agent_definition=agent_definition
            )
            simulator_agent = SimulatorAgent.objects.create(
                name=validated_data.get("name", "Default Agent"),
                prompt=agent_prompt,
                voice_provider=validated_data.get("voice_provider", "elevenlabs"),
                voice_name=validated_data.get("voice_name", "marissa"),
                model=validated_data.get("model", "gpt-4"),
                llm_temperature=validated_data.get("llm_temperature", 0.7),
                initial_message=validated_data.get("initial_message", ""),
                max_call_duration_in_minutes=validated_data.get(
                    "max_call_duration_in_minutes", 30
                ),
                interrupt_sensitivity=validated_data.get("interrupt_sensitivity", 0.5),
                conversation_speed=validated_data.get("conversation_speed", 1.0),
                finished_speaking_sensitivity=validated_data.get(
                    "finished_speaking_sensitivity", 0.5
                ),
                initial_message_delay=validated_data.get("initial_message_delay", 0),
                organization=getattr(request, "organization", None)
                or request.user.organization,
                workspace=(
                    request.user.workspace
                    if hasattr(request.user, "workspace")
                    else None
                ),
            )
            return simulator_agent
        except Exception as e:
            raise Exception(f"Failed to create simulator agent: {str(e)}")  # noqa: B904


class ScenarioDetailView(APIView):
    """
    API View to get details of a specific scenario
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Get scenario detail",
        operation_description="Returns full detail of a specific scenario including graph data and prompts.",
        responses={
            200: ScenarioDetailResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def get(self, request, scenario_id, *args, **kwargs):
        """
        Get details of a specific scenario with graph and prompts information
        """
        try:
            # Get the scenario
            scenario = get_object_or_404(
                Scenarios,
                id=scenario_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Handle prompt-based scenarios (no agent_definition)
            if scenario.agent_definition:
                agent_type = scenario.agent_definition.agent_type
                agent_type = (
                    agent_type
                    if agent_type is not None
                    else AgentDefinition.AgentTypeChoices.VOICE
                )
            else:
                # Prompt-based scenarios default to TEXT
                agent_type = AgentDefinition.AgentTypeChoices.TEXT

            # Build response data
            response_data = {
                "id": str(scenario.id),
                "name": scenario.name,
                "description": scenario.description,
                "source": scenario.source,
                "scenario_type": scenario.scenario_type,
                "dataset_id": str(scenario.dataset.id) if scenario.dataset else None,
                # "scenario_type_display": scenario.get_scenario_type_display(),
                "organization": str(scenario.organization.id),
                "dataset": str(scenario.dataset.id) if scenario.dataset else None,
                "created_at": scenario.created_at,
                "updated_at": scenario.updated_at,
                "deleted": scenario.deleted,
                "deleted_at": scenario.deleted_at,
                "status": scenario.status,
                "agent_type": agent_type,
            }

            # Add graph information for graph-type scenarios
            # if scenario.scenario_type == Scenarios.ScenarioTypes.GRAPH:
            graph_data = self._get_scenario_graph_data(scenario)
            response_data["graph"] = graph_data

            # Add prompts information from simulator agent
            prompts_data = self._get_scenario_prompts(scenario)
            response_data["prompts"] = prompts_data

            # Add dataset rows count for dataset-type scenarios
            if scenario.dataset:
                dataset_rows = Row.objects.filter(
                    dataset=scenario.dataset, deleted=False
                ).count()
                response_data["dataset_rows"] = dataset_rows
            else:
                response_data["dataset_rows"] = 0

            # Return the response — pass through serializer to whitelist permitted fields
            return Response(
                ScenarioDetailResponseSerializer(response_data).data,
                status=status.HTTP_200_OK,
            )

        except Http404:
            return Response(
                {"error": "Scenario not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to retrieve scenario: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _get_scenario_graph_data(self, scenario):
        """Get graph data for a scenario"""
        try:
            # Get the most recent active graph for this scenario
            graph = (
                ScenarioGraph.objects.filter(scenario=scenario, is_active=True)
                .order_by("-created_at")
                .first()
            )

            if graph and graph.graph_config:
                return graph.graph_config.get("graph_data", {})
            return {}

        except Exception as e:
            print(f"Error getting graph data: {e}")
            return {}

    def _get_scenario_prompts(self, scenario):
        """Get prompts for a scenario from simulator agent"""
        try:
            if scenario.simulator_agent:
                # Return the prompt as a structured format
                return [{"role": "system", "content": scenario.simulator_agent.prompt}]
            return []

        except Exception as e:
            print(f"Error getting prompts: {e}")
            return []


class DeleteScenarioView(APIView):
    """
    API View to delete a scenario (soft delete)
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Delete scenario",
        operation_description="Soft-deletes a scenario by setting deleted=True.",
        responses={
            200: ScenarioDeleteResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def delete(self, request, scenario_id, *args, **kwargs):
        """
        Soft delete a scenario by setting deleted=True
        """
        try:
            # Get the scenario
            scenario = get_object_or_404(
                Scenarios,
                id=scenario_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Soft delete the scenario
            scenario.deleted = True
            scenario.save()

            return Response(
                ScenarioDeleteResponseSerializer({"message": "Scenario deleted successfully"}).data,
                status=status.HTTP_200_OK,
            )

        except Http404:
            return Response(
                {"error": "Scenario not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to delete scenario: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EditScenarioView(APIView):
    """
    API View to edit a scenario's name and description
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Edit scenario",
        operation_description="Updates scenario name, description, graph, or prompt.",
        request_body=ScenarioEditRequestSerializer,
        responses={
            200: ScenarioEditResponseSerializer,
            400: ScenarioErrorResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def put(self, request, scenario_id, *args, **kwargs):
        """
        Update scenario name and description
        """
        try:
            # Get the scenario
            scenario = get_object_or_404(
                Scenarios,
                id=scenario_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Validate the data
            serializer = ScenarioEditRequestSerializer(data=request.data)

            if not serializer.is_valid():
                return self.gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            # Update the scenario
            if validated_data.get("name"):
                scenario.name = validated_data.get("name")
            if validated_data.get("description"):
                scenario.description = validated_data.get("description")
            if validated_data.get("graph"):
                # Handle graph update through ScenarioGraph model
                # Get the most recent active graph for this scenario
                scenario_graph = (
                    ScenarioGraph.objects.filter(scenario=scenario, is_active=True)
                    .order_by("-created_at")
                    .first()
                )

                if scenario_graph:
                    # Update existing graph's config
                    graph_config = scenario_graph.graph_config or {}
                    graph_config["graph_data"] = validated_data.get("graph")
                    scenario_graph.graph_config = graph_config
                    scenario_graph.save()
                else:
                    # Create new graph if none exists
                    ScenarioGraph.objects.create(
                        scenario=scenario,
                        name=f"{scenario.name} - Graph",
                        description=f"Graph for {scenario.name}",
                        organization=scenario.organization,
                        workspace=scenario.workspace,
                        graph_config={
                            "graph_data": validated_data.get("graph"),
                            "source": "user_provided",
                        },
                    )
            if validated_data.get("prompt"):
                scenario.simulator_agent.prompt = validated_data.get("prompt")
                scenario.simulator_agent.save()
            scenario.save()

            return Response(
                ScenarioEditResponseSerializer({
                    "message": "Scenario updated successfully",
                    "scenario": scenario,
                }).data,
                status=status.HTTP_200_OK,
            )

        except Http404:
            return Response(
                {"error": "Scenario not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to update scenario: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EditScenarioPromptsView(APIView):
    """
    API View to edit scenario prompts
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Edit scenario prompts",
        operation_description="Updates the simulator agent prompt for a scenario.",
        request_body=ScenarioEditPromptsRequestSerializer,
        responses={
            200: ScenarioPromptsUpdateResponseSerializer,
            400: ScenarioErrorResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def put(self, request, scenario_id, *args, **kwargs):
        """
        Update scenario prompts
        """
        try:
            # Get the scenario
            scenario = get_object_or_404(
                Scenarios,
                id=scenario_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Validate the data
            serializer = ScenarioEditPromptsRequestSerializer(data=request.data)

            if not serializer.is_valid():
                return self.gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            prompts = validated_data["prompts"]

            # # Update the simulator agent prompt
            # if scenario.simulator_agent:
            #     # Extract system prompt from prompts list
            #     system_prompt = ""
            #     for prompt in prompts:
            #         if prompt.get('role') == 'system':
            #             system_prompt = prompt.get('content', '')
            #             break

            # Update the simulator agent prompt
            scenario.simulator_agent.prompt = prompts
            scenario.simulator_agent.save()

            return Response(
                ScenarioPromptsUpdateResponseSerializer({
                    "message": "Scenario prompts updated successfully",
                    "prompts": prompts,
                }).data,
                status=status.HTTP_200_OK,
            )

        except Http404:
            return Response(
                {"error": "Scenario not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to update scenario prompts: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AddScenarioRowsView(APIView):
    """
    API View to add rows to a scenario dataset
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Add rows to scenario",
        operation_description="Adds new rows to a scenario's dataset via Temporal workflow. Returns 202 Accepted.",
        request_body=ScenarioAddRowsRequestSerializer,
        responses={
            202: ScenarioAddRowsResponseSerializer,
            400: ScenarioErrorResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def post(self, request, scenario_id, *args, **kwargs):
        """
        Add new rows to a scenario's dataset
        Expected payload:
        {
            "num_rows": int (required, 10-20000),
            "description": str (optional)
        }
        """
        from tfc.ee_gating import EEFeature, check_ee_feature

        org = getattr(request, "organization", None) or request.user.organization
        check_ee_feature(EEFeature.AGENTIC_EVAL, org_id=str(org.id))

        try:
            # Get the scenario
            scenario = get_object_or_404(
                Scenarios,
                id=scenario_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Check if scenario has a dataset
            if not scenario.dataset:
                return self.gm.bad_request(
                    "Scenario does not have an associated dataset."
                )

            # Validate the input data
            serializer = ScenarioAddRowsRequestSerializer(data=request.data)

            if not serializer.is_valid():
                return self.gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            num_rows = validated_data["num_rows"]
            description = validated_data.get("description", "")

            # Get the dataset
            dataset = scenario.dataset

            # Get all columns (excluding experiment columns)
            total_columns = Column.objects.filter(
                dataset=dataset, deleted=False
            ).exclude(
                source__in=[
                    SourceChoices.EXPERIMENT.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ]
            )

            # Get the max order for new rows
            max_order = (
                Row.objects.filter(dataset=dataset, deleted=False).aggregate(
                    Max("order")
                )["order__max"]
                or -1
            )

            # Bulk create new empty rows
            new_rows = []
            new_rows_id = []
            for i in range(num_rows):
                row_id = uuid.uuid4()
                new_rows.append(
                    Row(id=row_id, dataset=dataset, order=max_order + 1 + i)
                )
                new_rows_id.append(str(row_id))

            # Bulk create rows in a single query
            Row.objects.bulk_create(new_rows)

            # Bulk create empty cells for all columns
            new_cells = []
            for row_id in new_rows_id:
                for col in total_columns:
                    new_cells.append(
                        Cell(
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column=col,
                            row_id=row_id,
                            value=None,
                            status="running",
                        )
                    )

            # Bulk create cells in a single query
            Cell.objects.bulk_create(new_cells)

            # Update columns status to running in a single query
            total_columns.update(status=StatusType.RUNNING.value)

            # Trigger the Temporal workflow to generate the data
            start_add_scenario_rows_workflow_sync(
                dataset_id=str(dataset.id),
                scenario_id=str(scenario.id),
                num_rows=num_rows,
                row_ids=new_rows_id,
                description=description,
            )

            return Response(
                ScenarioAddRowsResponseSerializer({
                    "message": f"Started generating {num_rows} new rows for scenario",
                    "scenario_id": str(scenario_id),
                    "dataset_id": str(dataset.id),
                    "num_rows": num_rows,
                }).data,
                status=status.HTTP_202_ACCEPTED,
            )

        except Http404:
            return Response(
                {"error": "Scenario not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            return Response(
                {"error": f"Failed to add rows: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class AddScenarioColumnsView(APIView):
    """
    API View to add columns to a scenario dataset
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    @swagger_auto_schema(
        tags=["Scenarios"],
        operation_summary="Add columns to scenario",
        operation_description="Adds new columns to a scenario's dataset via Temporal workflow. Returns 202 Accepted.",
        request_body=ScenarioAddColumnsRequestSerializer,
        responses={
            202: ScenarioAddColumnsResponseSerializer,
            400: ScenarioErrorResponseSerializer,
            404: ScenarioErrorResponseSerializer,
            500: ScenarioErrorResponseSerializer,
        },
    )
    def post(self, request, scenario_id, *args, **kwargs):
        """
        Add new columns to a scenario's dataset
        Expected payload:
        {
            "columns": [
                {
                    "name": "column_name",
                    "data_type": "text",
                    "description": "column description"
                },
                ...
            ]
        }
        """
        try:
            try:
                try:
                    from ee.usage.services.entitlements import Entitlements
                except ImportError:
                    Entitlements = None

                org = (
                    getattr(request, "organization", None) or request.user.organization
                )
                feat_check = Entitlements.check_feature(str(org.id), "has_agentic_eval")
                if not feat_check.allowed:
                    return self.gm.forbidden_response(feat_check.reason)
            except ImportError:
                pass

            # Get the scenario
            scenario = get_object_or_404(
                Scenarios,
                id=scenario_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )

            # Check if scenario has a dataset
            if not scenario.dataset:
                return self.gm.bad_request(
                    "Scenario does not have an associated dataset."
                )

            # Phase 0.2: pass scenario via context so serializer validates duplicates
            serializer = ScenarioAddColumnsRequestSerializer(
                data=request.data,
                context={"request": request, "scenario": scenario},
            )

            if not serializer.is_valid():
                return self.gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            columns_info = validated_data["columns"]

            # Get the dataset
            dataset = scenario.dataset

            # Get all rows in the dataset
            all_rows = Row.objects.filter(dataset=dataset, deleted=False)
            row_ids = list(all_rows.values_list("id", flat=True))

            if not row_ids:
                return self.gm.bad_request(
                    "Dataset has no rows. Cannot add columns to an empty dataset."
                )

            # Create new columns
            new_columns = []
            new_column_ids = []
            for column in columns_info:
                column_id = uuid.uuid4()
                new_columns.append(
                    Column(
                        id=column_id,
                        dataset=dataset,
                        name=column["name"],
                        data_type=column["data_type"],
                        source=SourceChoices.OTHERS.value,
                        status=StatusType.RUNNING.value,
                        metadata={"description": column.get("description", "")},
                    )
                )
                new_column_ids.append(str(column_id))

            # Bulk create columns
            Column.objects.bulk_create(new_columns)

            # Update dataset column_order and column_config
            with transaction.atomic():
                dataset.refresh_from_db()
                current_column_order = dataset.column_order or []
                current_column_config = dataset.column_config or {}

                for col_id, col_info in zip(new_column_ids, columns_info):
                    current_column_order.append(col_id)
                    current_column_config[col_id] = {
                        "name": col_info["name"],
                        "type": col_info["data_type"],
                        "description": col_info.get("description", ""),
                    }

                dataset.column_order = current_column_order
                dataset.column_config = current_column_config
                dataset.save()

            # Bulk create empty cells for all rows and new columns
            new_cells = []
            for row_id in row_ids:
                for column_id in new_column_ids:
                    new_cells.append(
                        Cell(
                            id=uuid.uuid4(),
                            dataset=dataset,
                            column_id=column_id,
                            row_id=row_id,
                            value=None,
                            status=CellStatus.RUNNING.value,
                        )
                    )

            # Bulk create cells
            Cell.objects.bulk_create(new_cells)

            # Trigger the Temporal workflow to generate the column data
            start_add_columns_workflow_sync(
                dataset_id=str(dataset.id),
                scenario_id=str(scenario.id),
                columns_info=columns_info,
                column_ids=new_column_ids,
            )

            return Response(
                ScenarioAddColumnsResponseSerializer({
                    "message": f"Started generating {len(columns_info)} new column(s) for scenario",
                    "scenario_id": str(scenario_id),
                    "dataset_id": str(dataset.id),
                    "columns": [col["name"] for col in columns_info],
                }).data,
                status=status.HTTP_202_ACCEPTED,
            )

        except Http404:
            return Response(
                {"error": "Scenario not found."}, status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            return Response(
                {"error": f"Failed to add columns: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# DEPRECATED: Migrated to Temporal - CreateDatasetScenarioWorkflow
# This task is no longer called. Use start_create_dataset_scenario_workflow_sync() instead.
# @celery_app.task(
#     bind=True,
#     max_retries=0,
#     default_retry_delay=300,
#     time_limit=1800,  # 30 minutes
#     acks_late=False,
#     queue="tasks_xl",
# )
def _deprecated_create_dataset_scenario_background_task(
    user_id, validated_data, scenario_id
):
    """
    Celery task to create dataset scenario in background.

    Args:
        user_id: ID of the user creating the scenario
        validated_data: Validated data from the serializer
        scenario_id: ID of the scenario to update
    """
    try:
        close_old_connections()

        user = User.objects.get(id=user_id)
        scenario = Scenarios.objects.get(id=scenario_id)

        # Get the source dataset
        source_dataset = get_object_or_404(
            Dataset,
            id=validated_data["dataset_id"],
            deleted=False,
            organization=get_current_organization() or user.organization,
        )

        with transaction.atomic():
            # Create new dataset (copy of source dataset)
            new_dataset = Dataset.no_workspace_objects.create(
                id=uuid.uuid4(),
                name=f"Copy of {source_dataset.name}",
                organization=source_dataset.organization,
                workspace=scenario.workspace,
                model_type=source_dataset.model_type,
                column_order=(
                    source_dataset.column_order.copy()
                    if source_dataset.column_order
                    else []
                ),
                column_config=(
                    source_dataset.column_config.copy()
                    if source_dataset.column_config
                    else {}
                ),
                user=user,
                source=DatasetSourceChoices.SCENARIO.value,
            )

            # Copy columns
            column_id_mapping = {}
            new_columns = []

            source_columns = Column.objects.filter(
                dataset=source_dataset, deleted=False
            ).exclude(
                source__in=[
                    SourceChoices.EXPERIMENT.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                ]
            )

            # Get existing column names to check what's missing
            existing_column_names = set()
            for column in source_columns:
                existing_column_names.add(column.name)

            for column in source_columns:
                new_column_id = uuid.uuid4()
                column_id_mapping[str(column.id)] = str(new_column_id)

                new_columns.append(
                    Column(
                        id=new_column_id,
                        name=column.name,
                        data_type=column.data_type,
                        source=SourceChoices.OTHERS.value,
                        dataset=new_dataset,
                        deleted=False,
                    )
                )

            # Check if required scenario columns exist, add them if missing
            required_columns = {
                "persona": {
                    "data_type": DataTypeChoices.PERSONA.value,
                    "description": "Customer persona profile",
                },
                "situation": {
                    "data_type": DataTypeChoices.TEXT.value,
                    "description": "Customer situation or scenario",
                },
                "outcome": {
                    "data_type": DataTypeChoices.TEXT.value,
                    "description": "Conversation outcome",
                },
            }

            new_scenario_columns = {}
            for col_name, col_config in required_columns.items():
                if col_name not in existing_column_names:
                    new_column_id = uuid.uuid4()
                    new_scenario_columns[col_name] = new_column_id

                    new_columns.append(
                        Column(
                            id=new_column_id,
                            name=col_name,
                            data_type=col_config["data_type"],
                            source=SourceChoices.OTHERS.value,
                            dataset=new_dataset,
                            deleted=False,
                        )
                    )

            Column.objects.bulk_create(new_columns)

            # Update column_order with new column IDs
            new_column_order = []
            new_column_config = {}

            if new_dataset.column_order:
                for old_col_id in new_dataset.column_order:
                    if old_col_id in column_id_mapping:
                        new_column_order.append(column_id_mapping[old_col_id])
                        if (
                            new_dataset.column_config
                            and old_col_id in new_dataset.column_config
                        ):
                            new_column_config[column_id_mapping[old_col_id]] = (
                                new_dataset.column_config[old_col_id]
                            )

            # Add new scenario columns to column_order and column_config
            for col_name, col_id in new_scenario_columns.items():
                new_column_order.append(str(col_id))
                new_column_config[str(col_id)] = {
                    "name": col_name,
                    "type": required_columns[col_name]["data_type"],
                    "description": required_columns[col_name]["description"],
                }

            new_dataset.column_order = new_column_order
            new_dataset.column_config = new_column_config
            new_dataset.save()

            # Copy rows and cells in batches
            source_rows = Row.objects.filter(dataset=source_dataset, deleted=False)
            new_rows = []
            new_cells = []

            batch_size = 1000
            for i in range(0, source_rows.count(), batch_size):
                batch_rows = source_rows[i : i + batch_size]
                row_id_mapping = {}

                for row in batch_rows:
                    new_row_id = uuid.uuid4()
                    row_id_mapping[str(row.id)] = str(new_row_id)

                    new_rows.append(
                        Row(
                            id=new_row_id,
                            dataset=new_dataset,
                            order=row.order,
                            deleted=False,
                        )
                    )

                # Create rows in batch
                Row.objects.bulk_create(new_rows[-len(batch_rows) :])

                # Copy cells for this batch
                source_cells = Cell.objects.filter(row__in=batch_rows, deleted=False)

                for cell in source_cells:
                    if (
                        str(cell.column.id) in column_id_mapping
                        and str(cell.row.id) in row_id_mapping
                    ):
                        new_cells.append(
                            Cell(
                                id=uuid.uuid4(),
                                row_id=row_id_mapping[str(cell.row.id)],
                                column_id=column_id_mapping[str(cell.column.id)],
                                value=cell.value,
                                dataset=new_dataset,
                                deleted=False,
                            )
                        )

                # Create cells in batch
                Cell.objects.bulk_create(new_cells[-len(list(source_cells)) :])

            # Handle scenario columns - add cells for new columns if they were created
            if new_scenario_columns:
                source_rows_refs = Row.objects.filter(
                    dataset=new_dataset, deleted=False
                )

                # Get personas based on agent definition language
                # agent_language = "en"  # Default to English
                personas = []
                if scenario.agent_definition:
                    agent_definition = scenario.agent_definition
                    languages_array = agent_definition.languages
                    for language in languages_array:
                        personas_list = get_personas_by_language(language)
                        personas += personas_list

                # Fallback to English personas if no agent definition or empty personas
                if not personas:
                    personas = get_personas_by_language("en")

                scenario_cells = []
                for row_index, row in enumerate(source_rows_refs):
                    # Calculate which persona to use (cycle through the appropriate persona list)
                    persona_index = row_index % len(personas) if personas else 0
                    selected_persona = personas[persona_index] if personas else {}

                    # Create cells for new scenario columns
                    for col_name, col_id in new_scenario_columns.items():
                        if col_name == "persona":
                            cell_value = json.dumps(selected_persona)
                        elif col_name in ["situation", "outcome"]:
                            cell_value = ""  # Keep empty for now
                        else:
                            cell_value = ""

                        scenario_cells.append(
                            Cell(
                                id=uuid.uuid4(),
                                row=row,
                                column_id=col_id,
                                value=cell_value,
                                dataset=new_dataset,
                                deleted=False,
                            )
                        )

                # Batch create the new scenario column cells
                if scenario_cells:
                    Cell.objects.bulk_create(scenario_cells)

        # Update scenario with dataset and completed status
        scenario.dataset = new_dataset
        scenario.source = f"Created from dataset: {new_dataset.name}"
        scenario.status = StatusType.COMPLETED.value

        # Store persona_ids in metadata if provided
        persona_ids = validated_data.get("personas", [])
        if persona_ids:
            current_metadata = scenario.metadata if scenario.metadata else {}
            current_metadata["persona_ids"] = [str(pid) for pid in persona_ids]
            scenario.metadata = current_metadata

        scenario.save()

    except Exception as e:
        # Update scenario status to failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        raise Exception(f"Failed to create dataset scenario: {str(e)}") from e
    finally:
        close_old_connections()


# DEPRECATED: Migrated to Temporal - CreateScriptScenarioWorkflow
# This task is no longer called. Use start_create_script_scenario_workflow_sync() instead.
# @celery_app.task(
#     bind=True,
#     max_retries=0,
#     default_retry_delay=300,
#     time_limit=1800,  # 30 minutes
#     acks_late=False,
#     queue="tasks_xl",
# )
def _deprecated_create_script_scenario_background_task(validated_data, scenario_id):
    """
    Celery task to create script scenario in background.

    Args:
        validated_data: Validated data from the serializer
        scenario_id: ID of the scenario to update
    """
    try:
        close_old_connections()

        scenario = Scenarios.objects.get(id=scenario_id)

        no_of_rows = validated_data.get("no_of_rows", 20)
        script_url = validated_data.get("script_url")
        agent_definition_id = validated_data.get("agent_definition_id")
        persona_ids = validated_data.get("personas", [])
        custom_columns = validated_data.get("custom_columns", [])

        # persona_ids = ['c82e9449-ea6b-4d1c-ae33-f18378528cc3','cc55a95d-1334-42ce-a3fb-6be23c26f53c']
        script_content = ""

        # Convert persona IDs to property_list
        property_list = convert_personas_to_property_list(persona_ids)

        # Update scenario source and metadata with persona_ids
        scenario.source = script_content
        metadata = {"script_url": script_url}
        if persona_ids:
            metadata["persona_ids"] = [str(pid) for pid in persona_ids]
        scenario.metadata = json.dumps(metadata)
        scenario.save()

        # Handle graph generation or use provided graph data
        if agent_definition_id:
            # Generate graph using ConversationGraphGenerator
            try:
                graph_generator = ConversationGraphGenerator(
                    agent_definition_id=str(agent_definition_id),
                    scenario=scenario,
                    script_url=script_url,
                )
                generated_graph_data = graph_generator.generate_graph(save_to_db=True)

            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to generate graph: {str(e)}")  # noqa: B904

        enhanced_agent = EnhancedScenariosAgent(
            str(agent_definition_id),
            no_of_rows=no_of_rows,
            custom_columns=custom_columns,
        )
        s, d = enhanced_agent.run(
            name=scenario.name,
            description=scenario.description,
            user_requirements={},
            graph_id=str(generated_graph_data.id),
            property_list=property_list,
        )

        scenario.status = StatusType.COMPLETED.value
        scenario.dataset = d
        scenario.save()

    except Exception as e:
        # Update scenario status to failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        raise Exception(f"Failed to create script scenario: {str(e)}") from e
    finally:
        close_old_connections()


# DEPRECATED: Migrated to Temporal - CreateGraphScenarioWorkflow
# This task is no longer called. Use start_create_graph_scenario_workflow_sync() instead.
# @celery_app.task(
#     bind=True,
#     max_retries=0,
#     default_retry_delay=300,
#     time_limit=1800,  # 30 minutes
#     acks_late=False,
#     queue="tasks_xl",
# )
def _deprecated_create_graph_scenario_background_task(validated_data, scenario_id):
    """
    Celery task to create graph scenario in background.

    Args:
        validated_data: Validated data from the serializer
        scenario_id: ID of the scenario to update
    """
    try:
        close_old_connections()

        scenario = Scenarios.objects.get(id=scenario_id)

        agent_definition_id = validated_data.get("agent_definition_id")
        generate_graph = validated_data.get("generate_graph", False)
        graph_data = validated_data.get("graph")
        no_of_rows = validated_data.get("no_of_rows", 20)
        persona_ids = validated_data.get("personas", [])
        custom_columns = validated_data.get("custom_columns", [])

        # persona_ids = ['c82e9449-ea6b-4d1c-ae33-f18378528cc3','cc55a95d-1334-42ce-a3fb-6be23c26f53c']

        # Convert persona IDs to property_list
        property_list = convert_personas_to_property_list(persona_ids)

        # Update scenario source
        scenario.source = "Graph-based scenario"
        scenario.save()

        # Handle graph generation or use provided graph data
        if generate_graph and agent_definition_id:
            # Generate graph using ConversationGraphGenerator
            try:
                graph_generator = ConversationGraphGenerator(
                    agent_definition_id=str(agent_definition_id), scenario=scenario
                )
                scenario_graph = graph_generator.generate_graph(save_to_db=True)

            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to generate graph: {str(e)}")  # noqa: B904

        elif graph_data:
            # Use provided graph data
            try:
                # Create ScenarioGraph with provided data
                scenario_graph = ScenarioGraph.objects.create(
                    scenario=scenario,
                    name=f"{scenario.name} - Graph",
                    description=f"Graph for {scenario.name}",
                    organization=scenario.organization,
                    graph_config={"graph_data": graph_data, "source": "user_provided"},
                )

            except Exception as e:
                scenario.status = StatusType.FAILED.value
                scenario.save()
                raise Exception(f"Failed to save graph data: {str(e)}")  # noqa: B904

        enhanced_agent = EnhancedScenariosAgent(
            str(agent_definition_id),
            no_of_rows=no_of_rows,
            custom_columns=custom_columns,
        )
        s, d = enhanced_agent.run(
            name=scenario.name,
            description=scenario.description,
            user_requirements={},
            graph_id=str(scenario_graph.id),
            property_list=property_list,
        )
        print(s, d, "s,d123")

        scenario.status = StatusType.COMPLETED.value
        scenario.dataset = d

        # Store persona_ids in metadata if provided
        if persona_ids:
            current_metadata = scenario.metadata if scenario.metadata else {}
            if isinstance(current_metadata, str):
                current_metadata = json.loads(current_metadata)
            current_metadata["persona_ids"] = [str(pid) for pid in persona_ids]
            scenario.metadata = current_metadata

        scenario.save()

    except Exception as e:
        # Update scenario status to failed
        try:
            scenario = Scenarios.objects.get(id=scenario_id)
            scenario.status = StatusType.FAILED.value
            scenario.save()
        except Exception:
            pass
        raise Exception(f"Failed to create graph scenario: {str(e)}") from e
    finally:
        close_old_connections()
