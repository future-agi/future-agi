"""
Views for Prompt-based Simulations in Prompt Workbench.

These views provide API endpoints for creating and managing simulations
that use prompts as the agent source instead of SDK-based agent definitions.
"""

import structlog
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import RunTest, Scenarios, SimulateEvalConfig
from simulate.serializers.requests.run_test import CreatePromptSimulationSerializer
from simulate.serializers.run_test import RunTestSerializer
from simulate.services.test_executor import TestExecutor
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class PromptSimulationListCreateView(APIView):
    """
    API View to list and create prompt-based simulation runs for a prompt template.

    GET  - List paginated simulation runs
    POST - Create a new simulation run
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, prompt_template_id, *args, **kwargs):
        """
        Get paginated list of simulation runs for a specific prompt template.

        Query Parameters:
        - limit: number of items per page (default: 10)
        - page: page number (default: 1)
        - version_id: filter by specific prompt version
        """
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template exists and belongs to organization
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                deleted=False,
            )

            # Get query parameters
            version_id = request.query_params.get("version_id")
            limit = int(request.query_params.get("limit", 10))
            page = int(request.query_params.get("page", 1))

            # Filter run tests by prompt_template and source_type='prompt'
            run_tests = (
                RunTest.objects.filter(
                    prompt_template=prompt_template,
                    source_type="prompt",
                    organization=user_organization,
                    deleted=False,
                )
                .prefetch_related("scenarios")
                .order_by("-created_at")
            )

            # Filter by version if specified
            if version_id:
                run_tests = run_tests.filter(prompt_version_id=version_id)

            # Pagination
            total_count = run_tests.count()
            offset = (page - 1) * limit
            run_tests = run_tests[offset : offset + limit]

            # Serialize
            serializer = RunTestSerializer(run_tests, many=True)

            return self.gm.success_response(
                {
                    "count": total_count,
                    "page": page,
                    "limit": limit,
                    "results": serializer.data,
                    "prompt_template": {
                        "id": str(prompt_template.id),
                        "name": prompt_template.name,
                    },
                }
            )

        except ValueError as e:
            return self.gm.bad_request(str(e))
        except Exception as e:
            logger.exception("Error listing prompt simulations", error=str(e))
            return self.gm.internal_server_error_response("Failed to list simulations")

    def post(self, request, prompt_template_id, *args, **kwargs):
        """
        Create a new prompt-based simulation run.

        Request Body:
        - name: Name of the simulation run
        - description: Optional description
        - prompt_version_id: The prompt version to use
        - scenario_ids: List of scenario IDs to run
        - dataset_row_ids: Optional list of specific row IDs
        - evaluations_config: Optional evaluation configurations
        - enable_tool_evaluation: Optional boolean to enable tool evaluation
        """
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template exists and belongs to organization
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                deleted=False,
            )

            # Add prompt_template_id to request data
            data = request.data.copy()
            data["prompt_template_id"] = str(prompt_template_id)

            logger.info(
                "create_prompt_simulation_request",
                request_data=dict(data),
                prompt_template_id=str(prompt_template_id),
            )

            # Validate request data
            serializer = CreatePromptSimulationSerializer(
                data=data, context={"request": request}
            )
            if not serializer.is_valid():
                logger.warning(
                    "create_prompt_simulation_validation_failed",
                    errors=serializer.errors,
                    request_data=dict(data),
                )
                return self.gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            # Get the prompt version
            prompt_version = get_object_or_404(
                PromptVersion,
                id=validated_data["prompt_version_id"],
                original_template=prompt_template,
                deleted=False,
            )

            # Create the simulation run
            with transaction.atomic():
                # Get workspace from prompt template if available
                workspace = prompt_template.workspace

                run_test = RunTest.objects.create(
                    name=validated_data["name"],
                    description=validated_data.get("description", ""),
                    source_type="prompt",
                    prompt_template=prompt_template,
                    prompt_version=prompt_version,
                    agent_definition=None,  # Not used for prompt-based simulations
                    agent_version=None,
                    simulator_agent=None,
                    dataset_row_ids=validated_data.get("dataset_row_ids", []),
                    organization=user_organization,
                    workspace=workspace,
                    enable_tool_evaluation=validated_data.get(
                        "enable_tool_evaluation", False
                    ),
                )

                # Add scenarios
                scenarios = Scenarios.objects.filter(
                    id__in=validated_data["scenario_ids"],
                    organization=user_organization,
                )
                run_test.scenarios.set(scenarios)

                # Handle evaluations - create SimulateEvalConfig instances
                evaluations_config = validated_data.get("evaluations_config", [])

                if evaluations_config:
                    for eval_config_data in evaluations_config:
                        template_id = eval_config_data.get("template_id")

                        if template_id:
                            try:
                                eval_template = EvalTemplate.no_workspace_objects.get(
                                    id=template_id
                                )

                                SimulateEvalConfig.objects.create(
                                    eval_template=eval_template,
                                    name=eval_config_data.get(
                                        "name", f"Eval-{template_id}"
                                    ),
                                    config=eval_config_data.get("config", {}),
                                    mapping=eval_config_data.get("mapping", {}),
                                    run_test=run_test,
                                    filters=eval_config_data.get("filters", {}),
                                    error_localizer=eval_config_data.get(
                                        "error_localizer", False
                                    ),
                                    model=eval_config_data.get("model", None),
                                    eval_group_id=eval_config_data.get(
                                        "eval_group", None
                                    ),
                                )
                            except EvalTemplate.DoesNotExist:
                                continue

                logger.info(
                    "prompt_simulation_created",
                    run_test_id=str(run_test.id),
                    prompt_template_id=str(prompt_template_id),
                    prompt_version_id=str(prompt_version.id),
                    scenario_count=scenarios.count(),
                )

                # Serialize and return
                response_serializer = RunTestSerializer(run_test)
                return self.gm.success_response(
                    response_serializer.data, status=status.HTTP_201_CREATED
                )

        except Exception as e:
            logger.exception("Error creating prompt simulation", error=str(e))
            return self.gm.internal_server_error_response("Failed to create simulation")


class PromptSimulationDetailView(APIView):
    """
    API View to retrieve, update, or delete a specific prompt simulation run.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """Retrieve a specific prompt simulation run."""
        try:
            user_organization = get_request_organization(request)

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                deleted=False,
            )

            serializer = RunTestSerializer(run_test)
            return self.gm.success_response(serializer.data)

        except Http404:
            return self.gm.not_found("Simulation not found")
        except Exception as e:
            logger.exception("Error retrieving prompt simulation", error=str(e))
            return self.gm.internal_server_error_response(
                "Failed to retrieve simulation"
            )

    def patch(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """Update a prompt simulation run (version, scenarios, etc.)."""
        try:
            user_organization = get_request_organization(request)

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                deleted=False,
            )

            # Update prompt version if provided
            prompt_version_id = request.data.get("prompt_version_id")
            if prompt_version_id:
                prompt_version = get_object_or_404(
                    PromptVersion,
                    id=prompt_version_id,
                    original_template=prompt_template,
                    deleted=False,
                )
                run_test.prompt_version = prompt_version

            # Update scenarios if provided
            scenario_ids = request.data.get("scenario_ids")
            if scenario_ids is not None:
                scenarios = Scenarios.objects.filter(
                    id__in=scenario_ids,
                    organization=user_organization,
                    deleted=False,
                )
                run_test.scenarios.set(scenarios)

            # Update name if provided
            name = request.data.get("name")
            if name:
                run_test.name = name

            # Update description if provided
            description = request.data.get("description")
            if description is not None:
                run_test.description = description

            # Update enable_tool_evaluation if provided
            enable_tool_evaluation = request.data.get("enable_tool_evaluation")
            if enable_tool_evaluation is not None:
                run_test.enable_tool_evaluation = enable_tool_evaluation

            run_test.save()

            logger.info(
                "prompt_simulation_updated",
                run_test_id=str(run_test_id),
                prompt_template_id=str(prompt_template_id),
            )

            serializer = RunTestSerializer(run_test)
            return self.gm.success_response(serializer.data)

        except Http404:
            return self.gm.not_found("Simulation or related resource not found")
        except Exception as e:
            logger.exception("Error updating prompt simulation", error=str(e))
            return self.gm.internal_server_error_response("Failed to update simulation")

    def delete(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """Soft delete a prompt simulation run."""
        try:
            user_organization = get_request_organization(request)

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                deleted=False,
            )

            # Soft delete
            run_test.deleted = True
            run_test.save(update_fields=["deleted", "updated_at"])

            logger.info(
                "prompt_simulation_deleted",
                run_test_id=str(run_test_id),
                prompt_template_id=str(prompt_template_id),
            )

            return self.gm.success_response("Simulation run deleted successfully")

        except Exception as e:
            logger.exception("Error deleting prompt simulation", error=str(e))
            return self.gm.internal_server_error_response("Failed to delete simulation")


class ExecutePromptSimulationView(APIView):
    """
    API View to execute a prompt-based simulation run.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def post(self, request, prompt_template_id, run_test_id, *args, **kwargs):
        """
        Execute a prompt-based simulation run.

        Request Body (optional):
        - scenario_ids: List of specific scenario IDs to run (default: all scenarios)
        - select_all: If true, run all scenarios except ones in scenario_ids
        """
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Verify prompt template
            prompt_template = get_object_or_404(
                PromptTemplate,
                id=prompt_template_id,
                organization=user_organization,
                deleted=False,
            )

            # Get the run test
            run_test = get_object_or_404(
                RunTest,
                id=run_test_id,
                prompt_template=prompt_template,
                source_type="prompt",
                organization=user_organization,
                deleted=False,
            )

            # Validate prompt version still exists before execution
            if not run_test.prompt_version or run_test.prompt_version.deleted:
                return self.gm.bad_request(
                    "Prompt version has been deleted. Please update the simulation with a valid version."
                )

            # Get parameters from request
            scenario_ids = request.data.get("scenario_ids", [])
            select_all = request.data.get("select_all", False)

            # Get all available scenario IDs linked to this run test
            all_scenario_ids = list(
                run_test.scenarios.filter(deleted=False).values_list("id", flat=True)
            )

            # Determine which scenarios to execute
            if select_all:
                if scenario_ids:
                    # Exclude the provided scenario_ids from all scenarios
                    final_scenario_ids = [
                        str(scenario_id)
                        for scenario_id in all_scenario_ids
                        if str(scenario_id) not in scenario_ids
                    ]
                else:
                    # Run on all scenarios
                    final_scenario_ids = [
                        str(scenario_id) for scenario_id in all_scenario_ids
                    ]
            else:
                if scenario_ids:
                    final_scenario_ids = scenario_ids
                else:
                    # Run on all linked scenarios
                    final_scenario_ids = [
                        str(scenario_id) for scenario_id in all_scenario_ids
                    ]

            # Use the existing TestExecutor
            test_executor = TestExecutor()

            result = test_executor.execute_test(
                run_test_id=str(run_test.id),
                user_id=str(request.user.id),
                scenario_ids=final_scenario_ids,
                simulator_id=None,
            )

            if result["success"]:
                logger.info(
                    "prompt_simulation_execution_started",
                    run_test_id=str(run_test_id),
                    execution_id=result["execution_id"],
                    prompt_template_id=str(prompt_template_id),
                )

                return self.gm.success_response(
                    {
                        "message": "Simulation execution started successfully",
                        "execution_id": result["execution_id"],
                        "run_test_id": result["run_test_id"],
                        "status": result["status"],
                        "total_scenarios": result["total_scenarios"],
                        "total_calls": result["total_calls"],
                        "scenario_ids": final_scenario_ids,
                    }
                )
            else:
                return self.gm.bad_request(result["error"])

        except Exception as e:
            logger.exception("Error executing prompt simulation", error=str(e))
            return self.gm.internal_server_error_response(
                "Failed to execute simulation"
            )


class PromptSimulationScenariosView(APIView):
    """
    API View to list available scenarios for prompt simulations.

    Returns scenarios that can be used with prompt-based simulations,
    filtered by organization.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    def get(self, request, *args, **kwargs):
        """
        Get list of scenarios available for prompt simulations.

        Query Parameters:
        - limit: number of items per page (default: 20)
        - page: page number (default: 1)
        - search: search string to filter scenarios by name
        """
        try:
            user_organization = get_request_organization(request)

            if not user_organization:
                return self.gm.not_found("Organization not found for the user.")

            # Get query parameters
            search_query = request.query_params.get("search", "").strip()
            limit = int(request.query_params.get("limit", 20))
            page = int(request.query_params.get("page", 1))

            # Get scenarios
            scenarios = Scenarios.objects.filter(
                organization=user_organization,
                deleted=False,
            ).order_by("-created_at")

            # Apply search filter
            if search_query:
                scenarios = scenarios.filter(name__icontains=search_query)

            # Pagination
            total_count = scenarios.count()
            offset = (page - 1) * limit
            scenarios = scenarios[offset : offset + limit]

            # Serialize with minimal fields for selection
            scenarios_data = [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "description": s.description,
                    "scenario_type": s.scenario_type,
                    "dataset_id": str(s.dataset_id) if s.dataset_id else None,
                    "created_at": s.created_at.isoformat(),
                }
                for s in scenarios
            ]

            return self.gm.success_response(
                {
                    "count": total_count,
                    "page": page,
                    "limit": limit,
                    "results": scenarios_data,
                }
            )

        except Exception as e:
            logger.exception("Error listing scenarios", error=str(e))
            return self.gm.internal_server_error_response("Failed to list scenarios")
