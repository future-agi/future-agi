import structlog
from django.db import transaction
from django.db.models import Avg, Count
from django.http import Http404
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from model_hub.utils.dataset_optimization import calculate_percentage_point_change
from model_hub.utils.llm_providers import get_provider_logo_url
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace_filter,
)
from simulate.constants.agent_prompt_optimiser import (
    AGENT_PROMPT_OPTIMISER_RUN_TABLE_CONFIG,
    TRIAL_TABLE_EVAL_COLUMNS,
    TRIAL_TABLE_SCENARIOS_COLUMNS,
)
from simulate.models import (
    AgentPromptOptimiserRun,
    ComponentEvaluation,
    PromptTrial,
    TrialItemResult,
)
from simulate.serializers.agent_prompt_optimiser import (
    OPTIMISER_REQUIRED_CONFIG_KEYS,
    AgentPromptOptimiserGraphResponseSerializer,
    AgentPromptOptimiserRunCreateSerializer,
    AgentPromptOptimiserRunDetailResponseSerializer,
    AgentPromptOptimiserRunListSerializer,
    AgentPromptOptimiserRunModelResponseSerializer,
    AgentPromptOptimiserRunSerializer,
    AgentPromptOptimiserRunStepsResponseSerializer,
    AgentPromptOptimiserTrialEvaluationsResponseSerializer,
    AgentPromptOptimiserTrialPromptResponseSerializer,
    AgentPromptOptimiserTrialScenariosResponseSerializer,
)
from simulate.utils.agent_prompt_optimiser import (
    build_trial_table_data,
    create_agent_prompt_optimiser_run_steps,
    get_agent_prompt_optimiser_run_graph_data,
    get_agent_prompt_optimiser_run_steps,
)
from tfc.temporal.agent_prompt_optimiser.client import (
    start_agent_prompt_optimiser_workflow,
)
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.errors import format_validation_error
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# Human-readable labels and descriptions for optimizer configuration parameters
OPTIMISER_PARAM_META = {
    "num_gradients": {
        "label": "No. of gradients",
        "description": "The number of gradient descent steps used to optimize the prompt in each iteration.",
    },
    "errors_per_gradient": {
        "label": "Error per gradient",
        "description": "The number of error examples sampled per gradient step.",
    },
    "prompts_per_gradient": {
        "label": "Prompt per gradient",
        "description": "The number of prompt candidates generated per gradient step.",
    },
    "beam_size": {
        "label": "Beam Size",
        "description": "The number of top prompt candidates retained at each step.",
    },
    "num_rounds": {
        "label": "No. of rounds",
        "description": "The total number of optimization rounds to run.",
    },
    "num_variations": {
        "label": "No. of variations",
        "description": "The number of prompt variations to generate.",
    },
    "max_metric_calls": {
        "label": "Max metric calls",
        "description": "The maximum number of metric evaluation calls allowed.",
    },
    "min_examples": {
        "label": "Min examples",
        "description": "The minimum number of examples to use for optimization.",
    },
    "max_examples": {
        "label": "Max examples",
        "description": "The maximum number of examples to use for optimization.",
    },
    "n_trials": {
        "label": "No. of trials",
        "description": "The number of Bayesian optimization trials to run.",
    },
    "task_description": {
        "label": "Task description",
        "description": "A description of the task for the meta-prompt optimizer.",
    },
    "mutate_rounds": {
        "label": "Mutate rounds",
        "description": "The number of mutation rounds to apply.",
    },
    "refine_iterations": {
        "label": "Refine iterations",
        "description": "The number of refinement iterations per round.",
    },
}


def build_optimiser_parameters(optimiser_type, configuration):
    """Build a list of labeled parameters from the optimizer configuration."""
    if not configuration or not optimiser_type:
        return []

    config_keys = OPTIMISER_REQUIRED_CONFIG_KEYS.get(optimiser_type.lower(), [])
    parameters = []
    for key in config_keys:
        value = configuration.get(key)
        if value is None:
            continue
        meta = OPTIMISER_PARAM_META.get(key, {})
        parameters.append(
            {
                "key": key,
                "label": meta.get("label", key),
                "value": value,
                "description": meta.get("description", ""),
            }
        )
    return parameters


class AgentPromptOptimiserRunViewSet(BaseModelViewSetMixin, ModelViewSet):
    """
    ViewSet for Agent Prompt Optimiser Runs.

    Endpoints:
    - GET    /agent-prompt-optimiser/                                    - List all runs
    - POST   /agent-prompt-optimiser/                                    - Create a new run
    - GET    /agent-prompt-optimiser/{id}/                               - Get run details with trials table
    - GET    /agent-prompt-optimiser/{id}/steps/                          - Get run steps
    - GET    /agent-prompt-optimiser/{id}/graph/                          - Get run graph data
    - GET    /agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/       - Get trial & baseline prompts
    - GET    /agent-prompt-optimiser/{id}/trial/{trial_id}/evaluations/  - Get evaluations by eval_config
    - GET    /agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/    - Get scenarios (TrialItemResults)
    """

    queryset = AgentPromptOptimiserRun.objects.all()
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = AgentPromptOptimiserRunSerializer

    def get_queryset(self):
        queryset = (
            super()
            .get_queryset()
            .filter(
                test_execution__run_test__organization=request_organization(
                    self.request
                )
            )
            .filter(
                request_workspace_filter(
                    self.request,
                    field_name="test_execution__run_test__workspace",
                )
            )
            .order_by("-created_at")
        )

        # Optional test_execution filter
        test_execution_id = self.request.query_params.get("test_execution_id")
        if test_execution_id:
            queryset = queryset.filter(test_execution_id=test_execution_id)

        if self.action == "list":
            queryset = queryset.annotate(trial_count=Count("trials"))

        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return AgentPromptOptimiserRunCreateSerializer
        if self.action == "list":
            return AgentPromptOptimiserRunListSerializer
        return AgentPromptOptimiserRunSerializer

    @swagger_auto_schema(
        responses={
            400: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def list(self, request, *args, **kwargs):
        """
        List all agent prompt optimiser runs with table config for dynamic columns.
        """
        try:
            queryset = self.get_queryset()
            total_rows = queryset.count()

            # Apply pagination if configured
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
            else:
                serializer = self.get_serializer(queryset, many=True)

            return self._gm.success_response(
                {
                    "metadata": {"total_rows": total_rows},
                    "table": serializer.data,
                    "column_config": AGENT_PROMPT_OPTIMISER_RUN_TABLE_CONFIG,
                }
            )
        except Exception as e:
            logger.exception(f"Error listing AgentPromptOptimiserRuns: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserRunModelResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserRunModelResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        responses={
            400: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(get_error_message("INVALID_REQUEST_DATA"))

            self.perform_create(serializer)
            run_instance = serializer.instance

            create_agent_prompt_optimiser_run_steps(str(run_instance.id))
            try:
                start_agent_prompt_optimiser_workflow(str(run_instance.id))
            except Exception as e:
                logger.exception(
                    f"Failed to start Temporal workflow for AgentPromptOptimiserRun {run_instance.id}: {e}"
                )
                # Mark run as failed
                run_instance.mark_as_failed(
                    error_message=get_error_message("FAILED_TO_OPTIMISE_PROMPT")
                )
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_OPTIMISE_PROMPT")
                )

            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED,
            )

        except serializers.ValidationError as e:
            return self._gm.bad_request(format_validation_error(e))
        except Exception as e:
            logger.exception(f"Error creating AgentPromptOptimiserRun: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserRunDetailResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def retrieve(self, request, *args, **kwargs):
        """
        Get run details with trial comparison table.
        """
        try:
            instance = self.get_object()
            workspace = instance.test_execution.run_test.workspace
            workspace_id = workspace.id if workspace else None
            organization_id = instance.test_execution.run_test.organization.id

            # Build trial table data with eval comparisons
            table_data, column_config = build_trial_table_data(instance)
            provider_logo = get_provider_logo_url(
                instance.model, organization_id, workspace_id
            )

            # Build labeled parameters for display
            parameters = build_optimiser_parameters(
                instance.optimiser_type, instance.configuration
            )

            return self._gm.success_response(
                {
                    "optimiser_name": instance.name,
                    "optimiser_type": instance.optimiser_type,
                    "model": instance.model,
                    "provider_logo": provider_logo,
                    "configuration": instance.configuration,
                    "parameters": parameters,
                    "start_time": instance.created_at,
                    "status": instance.status,
                    "error_message": instance.error_message,
                    "table": table_data,
                    "column_config": column_config,
                }
            )
        except Http404:
            return self._gm.not_found("Agent prompt optimiser run not found")
        except Exception as e:
            logger.exception(f"Error retrieving AgentPromptOptimiserRun: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserRunStepsResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"])
    def steps(self, request, *args, **kwargs):
        """Get all steps for this optimization run."""
        try:
            instance = self.get_object()
            steps = get_agent_prompt_optimiser_run_steps(str(instance.id))
            return self._gm.success_response(steps)
        except Http404:
            return self._gm.not_found("Agent prompt optimiser run not found")
        except Exception as e:
            logger.exception(
                f"Error retrieving agent prompt optimiser run steps: {str(e)}"
            )
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserGraphResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"])
    def graph(self, request, *args, **kwargs):
        """
        Get graph data for this optimization run.
        """
        try:
            instance = self.get_object()
            graph_data = get_agent_prompt_optimiser_run_graph_data(instance)
            return self._gm.success_response(graph_data)
        except Http404:
            return self._gm.not_found("Agent prompt optimiser run not found")
        except Exception as e:
            logger.exception(
                f"Error retrieving agent prompt optimiser run graph data: {str(e)}"
            )
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    def _get_trial_header_data(self, optimiser_run, trial):
        """Get common header data for trial detail APIs."""
        baseline_trial = optimiser_run.trials.filter(is_baseline=True).first()
        baseline_score = baseline_trial.average_score if baseline_trial else None

        score_percentage_change = calculate_percentage_point_change(
            trial.average_score, baseline_score
        )

        return {
            "trial_name": f"Trial {trial.trial_number}",
            "optimisation_name": optimiser_run.name,
            "created_at": optimiser_run.created_at,
            "score": (
                round(trial.average_score, 4)
                if trial.average_score is not None
                else None
            ),
            "score_percentage_change": score_percentage_change,
        }

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserTrialPromptResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)/prompt",
    )
    def trial_prompt(self, request, trial_id=None, *args, **kwargs):
        """
        Get trial prompt and baseline prompt.

        URL: GET /agent-prompt-optimiser/{id}/trial/{trial_id}/prompt/
        """
        try:
            instance = self.get_object()
            trial = instance.trials.get(id=trial_id)
            baseline_trial = instance.trials.filter(is_baseline=True).first()

            header = self._get_trial_header_data(instance, trial)

            return self._gm.success_response(
                {
                    **header,
                    "trial_prompt": trial.prompt,
                    "base_prompt": baseline_trial.prompt if baseline_trial else None,
                }
            )
        except Http404:
            return self._gm.not_found("Agent prompt optimiser run not found")
        except PromptTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial prompt: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserTrialEvaluationsResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)/evaluations",
    )
    def trial_evaluations(self, request, trial_id=None, *args, **kwargs):
        """
        Get evaluations for a trial grouped by eval_config.

        URL: GET /agent-prompt-optimiser/{id}/trial/{trial_id}/evaluations/
        """
        try:
            instance = self.get_object()
            trial = instance.trials.get(id=trial_id)
            baseline_trial = instance.trials.filter(is_baseline=True).first()

            header = self._get_trial_header_data(instance, trial)

            baseline_eval_scores = {}
            if baseline_trial:
                baseline_evals = (
                    ComponentEvaluation.objects.filter(
                        trial_item_result__prompt_trial=baseline_trial
                    )
                    .values("eval_config__id")
                    .annotate(avg_score=Avg("score"))
                )

                for eval_data in baseline_evals:
                    baseline_eval_scores[eval_data["eval_config__id"]] = eval_data[
                        "avg_score"
                    ]

            # Get trial evaluations grouped by eval_config
            trial_evals = (
                ComponentEvaluation.objects.filter(
                    trial_item_result__prompt_trial=trial
                )
                .values(
                    "eval_config__id",
                    "eval_config__name",
                    "eval_config__eval_template__description",
                )
                .annotate(avg_score=Avg("score"))
            )

            # Build table data
            table_data = []
            for eval_data in trial_evals:
                eval_config_id = eval_data["eval_config__id"]
                eval_score = eval_data["avg_score"]
                baseline_eval_score = baseline_eval_scores.get(eval_config_id)

                percentage_change = calculate_percentage_point_change(
                    eval_score, baseline_eval_score
                )

                table_data.append(
                    {
                        "id": str(eval_config_id),
                        "eval_name": eval_data["eval_config__name"],
                        "eval_template_description": eval_data[
                            "eval_config__eval_template__description"
                        ],
                        "score": (
                            round(eval_score, 4) if eval_score is not None else None
                        ),
                        "score_percentage_change": percentage_change,
                    }
                )

            return self._gm.success_response(
                {
                    **header,
                    "table": table_data,
                    "column_config": TRIAL_TABLE_EVAL_COLUMNS,
                }
            )
        except Http404:
            return self._gm.not_found("Agent prompt optimiser run not found")
        except PromptTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial evaluations: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            200: AgentPromptOptimiserTrialScenariosResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)/scenarios",
    )
    def trial_scenarios(self, request, trial_id=None, *args, **kwargs):
        """
        Get scenarios (TrialItemResults) for a trial with eval scores.

        URL: GET /agent-prompt-optimiser/{id}/trial/{trial_id}/scenarios/
        """
        try:
            instance = self.get_object()
            trial = instance.trials.get(id=trial_id)

            header = self._get_trial_header_data(instance, trial)

            trial_items = (
                TrialItemResult.objects.filter(prompt_trial=trial)
                .prefetch_related("component_evaluations__eval_config")
                .order_by("created_at")
            )

            eval_configs = {}
            table_data = []

            for item in trial_items:
                row = {
                    "id": str(item.id),
                    "input_text": item.input_text,
                    "output_text": item.output_text,
                }

                for comp_eval in item.component_evaluations.all():
                    eval_config_id = str(comp_eval.eval_config.id)
                    eval_configs[eval_config_id] = comp_eval.eval_config.name
                    row[eval_config_id] = (
                        round(comp_eval.score, 4)
                        if comp_eval.score is not None
                        else None
                    )

                table_data.append(row)

            eval_columns = [
                {"id": eval_id, "name": eval_name, "is_visible": True}
                for eval_id, eval_name in eval_configs.items()
            ]

            column_config = list(TRIAL_TABLE_SCENARIOS_COLUMNS) + eval_columns

            return self._gm.success_response(
                {
                    **header,
                    "table": table_data,
                    "column_config": column_config,
                }
            )
        except Http404:
            return self._gm.not_found("Agent prompt optimiser run not found")
        except PromptTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial scenarios: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        }
    )
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)
