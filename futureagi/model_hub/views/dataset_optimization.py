"""
ViewSet for Dataset Optimization

Following the same patterns as simulate.views.agent_prompt_optimiser.
"""

import structlog
from django.db import transaction
from django.http import Http404
from django.db.models import Avg, Count, Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.dataset_optimization_trial_item import (
    DatasetOptimizationItemEvaluation,
    DatasetOptimizationTrialItem,
)
from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
from model_hub.models.optimize_dataset import OptimizeDataset
from drf_yasg.utils import swagger_auto_schema

from model_hub.serializers.dataset_optimization import (
    DatasetOptimizationCreateSerializer,
    DatasetOptimizationDetailApiResponseSerializer,
    DatasetOptimizationDetailSerializer,
    DatasetOptimizationListSerializer,
    DatasetOptimizationSerializer,
    DatasetOptimizationTrialSerializer,
)
from model_hub.utils.dataset_optimization import (
    OPTIMIZATION_RUN_TABLE_CONFIG,
    TRIAL_TABLE_BASE_COLUMNS,
    calculate_percentage_point_change,
    create_dataset_optimization_steps,
    get_dataset_optimization_steps,
    get_optimization_graph_data,
)
from tfc.temporal.dataset_optimization.client import (
    cancel_dataset_optimization,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.error_codes import get_error_message
from tfc.utils.errors import format_validation_error
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _request_workspace_filter(request, field_name="column__dataset__workspace"):
    workspace = getattr(request, "workspace", None)
    if workspace is None:
        return Q()
    if getattr(workspace, "is_default", False):
        organization = getattr(workspace, "organization", None)
        query = Q(**{field_name: workspace})
        if organization is not None:
            query |= Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization": organization,
                }
            )
        query |= Q(**{f"{field_name}__isnull": True})
        return query
    return Q(**{field_name: workspace})


class DatasetOptimizationViewSet(BaseModelViewSetMixin, ModelViewSet):
    """
    ViewSet for Dataset Optimization Runs.

    Endpoints:
    - GET    /dataset-optimization/                                    - List all runs
    - POST   /dataset-optimization/                                    - Create a new run
    - GET    /dataset-optimization/{id}/                               - Get run details with trials table
    - GET    /dataset-optimization/{id}/steps/                         - Get run steps
    - GET    /dataset-optimization/{id}/graph/                         - Get run graph data
    - GET    /dataset-optimization/{id}/trial/{trial_id}/prompt/       - Get trial & baseline prompts
    """

    queryset = OptimizeDataset.objects.all()
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()
    serializer_class = DatasetOptimizationSerializer

    def initial(self, request, *args, **kwargs):
        """Dataset optimization is EE-only in OSS pricing."""
        from tfc.ee_gating import EEFeature, check_ee_feature

        super().initial(request, *args, **kwargs)
        org = getattr(request, "organization", None) or request.user.organization
        check_ee_feature(EEFeature.OPTIMIZATION, org_id=str(org.id))

    def get_queryset(self):
        user_organization = (
            getattr(self.request, "organization", None)
            or self.request.user.organization
        )
        # Filter by organization via column -> dataset relationship
        # Note: We don't call super().get_queryset() because BaseModelViewSetMixin
        # adds a deleted=False filter, but OptimizeDataset doesn't have that field
        queryset = (
            self.queryset.filter(
                column__dataset__organization=user_organization,
                column__dataset__deleted=False,
                column__deleted=False,
            )
            .filter(_request_workspace_filter(self.request))
            .order_by("-created_at")
        )

        # Optional dataset filter
        dataset_id = self.request.query_params.get("dataset_id")
        if dataset_id:
            queryset = queryset.filter(column__dataset_id=dataset_id)

        # Optional column filter
        column_id = self.request.query_params.get("column_id")
        if column_id:
            queryset = queryset.filter(column_id=column_id)

        # Optional develop filter
        develop_id = self.request.query_params.get("develop_id")
        if develop_id:
            queryset = queryset.filter(develop_id=develop_id)

        # Filter for new optimization flow only (with optimizer_algorithm set)
        queryset = queryset.filter(optimizer_algorithm__isnull=False)

        if self.action == "list":
            queryset = queryset.annotate(trial_count=Count("trials"))

        return queryset

    def perform_destroy(self, instance):
        deleted_at = timezone.now()
        DatasetOptimizationItemEvaluation.objects.filter(
            trial_item__trial__optimization_run=instance,
            deleted=False,
        ).update(deleted=True, deleted_at=deleted_at)
        DatasetOptimizationTrialItem.objects.filter(
            trial__optimization_run=instance,
            deleted=False,
        ).update(deleted=True, deleted_at=deleted_at)
        DatasetOptimizationTrial.objects.filter(
            optimization_run=instance,
            deleted=False,
        ).update(deleted=True, deleted_at=deleted_at)
        DatasetOptimizationStep.objects.filter(
            optimization_run=instance,
            deleted=False,
        ).update(deleted=True, deleted_at=deleted_at)
        instance.deleted = True
        instance.deleted_at = deleted_at
        instance.save(update_fields=["deleted", "deleted_at"])

    def get_serializer_class(self):
        if self.action == "create":
            return DatasetOptimizationCreateSerializer
        if self.action == "list":
            return DatasetOptimizationListSerializer
        if self.action == "retrieve":
            return DatasetOptimizationDetailSerializer
        return DatasetOptimizationSerializer

    def list(self, request, *args, **kwargs):
        """
        List all dataset optimization runs with table config for dynamic columns.
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
                    "column_config": OPTIMIZATION_RUN_TABLE_CONFIG,
                }
            )
        except Exception as e:
            logger.exception(f"Error listing DatasetOptimizationRuns: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(format_validation_error(serializer.errors))

            self.perform_create(serializer)
            run_instance = serializer.instance

            # Create optimization steps
            create_dataset_optimization_steps(str(run_instance.id))

            # Start the Temporal workflow
            try:
                from tfc.temporal.dataset_optimization.client import (
                    start_dataset_optimization_workflow,
                )

                start_dataset_optimization_workflow(str(run_instance.id))
            except Exception as e:
                logger.exception(
                    f"Failed to start Temporal workflow for DatasetOptimization {run_instance.id}: {e}"
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
            logger.exception(f"Error creating DatasetOptimization: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @swagger_auto_schema(
        responses={200: DatasetOptimizationDetailApiResponseSerializer}
    )
    def retrieve(self, request, *args, **kwargs):
        """Get run details; payload matches AgentPromptOptimiserRunViewSet.retrieve()."""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return self._gm.success_response(serializer.data)
        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.exception(f"Error retrieving DatasetOptimization: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @action(detail=True, methods=["get"])
    def steps(self, request, *args, **kwargs):
        """Get all steps for this optimization run."""
        try:
            instance = self.get_object()
            steps = get_dataset_optimization_steps(str(instance.id))
            return self._gm.success_response(steps)
        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.exception(f"Error retrieving dataset optimization steps: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @action(detail=True, methods=["get"])
    def graph(self, request, *args, **kwargs):
        """
        Get graph data for this optimization run.
        """
        try:
            instance = self.get_object()
            graph_data = get_optimization_graph_data(instance)
            return self._gm.success_response(graph_data)
        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.exception(
                f"Error retrieving dataset optimization graph data: {str(e)}"
            )
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @action(detail=True, methods=["post"])
    def stop(self, request, *args, **kwargs):
        """
        Stop a running dataset optimization.

        URL: POST /dataset-optimization/{id}/stop/
        """
        try:
            # Use a short-lived transaction only for status validation to avoid
            # holding a DB connection and row lock during the external network call.
            with transaction.atomic():
                instance = self.get_object()
                instance = OptimizeDataset.objects.select_for_update(of=("self",)).get(
                    pk=instance.pk,
                    deleted=False,
                )

                cancellable_statuses = [
                    OptimizeDataset.StatusType.RUNNING,
                    OptimizeDataset.StatusType.PENDING,
                ]
                if instance.status not in cancellable_statuses:
                    return self._gm.bad_request(
                        f"Cannot stop optimization with status: {instance.status}"
                    )

            # Cancel the Temporal workflow outside the transaction so the DB lock
            # is not held during the network call.
            workflow_cancelled = cancel_dataset_optimization(str(instance.id))
            if not workflow_cancelled:
                return self._gm.bad_request(
                    "Failed to cancel optimization. Please try again."
                )

            instance.mark_as_cancelled()
            return self._gm.success_response(
                {
                    "success": True,
                    "message": "Optimization cancelled successfully",
                    "id": str(instance.id),
                }
            )

        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.exception(f"Error stopping dataset optimization: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    def _get_trial_header_data(self, optimization_run, trial):
        """Get common header data for trial detail APIs."""
        baseline_trial = optimization_run.trials.filter(is_baseline=True).first()
        baseline_score = baseline_trial.average_score if baseline_trial else None

        score_percentage_change = calculate_percentage_point_change(
            trial.average_score, baseline_score
        )

        return {
            "trial_name": f"Trial {trial.trial_number}",
            "optimization_name": optimization_run.name,
            "created_at": optimization_run.created_at,
            "score": (
                round(trial.average_score, 4)
                if trial.average_score is not None
                else None
            ),
            "score_percentage_change": score_percentage_change,
        }

    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)/prompt",
    )
    def trial_prompt(self, request, trial_id=None, *args, **kwargs):
        """
        Get trial prompt and baseline prompt.

        URL: GET /dataset-optimization/{id}/trial/{trial_id}/prompt/
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
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DatasetOptimizationTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial prompt: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)",
    )
    def trial_detail(self, request, trial_id=None, *args, **kwargs):
        """
        Get full trial details.

        URL: GET /dataset-optimization/{id}/trial/{trial_id}/
        """
        try:
            instance = self.get_object()
            trial = instance.trials.get(id=trial_id)

            header = self._get_trial_header_data(instance, trial)
            trial_serializer = DatasetOptimizationTrialSerializer(trial)

            return self._gm.success_response(
                {
                    **header,
                    "trial": trial_serializer.data,
                }
            )
        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DatasetOptimizationTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial detail: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)/scenarios",
    )
    def trial_scenarios(self, request, trial_id=None, *args, **kwargs):
        """
        Get scenarios (individual evaluation results) for a trial.

        URL: GET /dataset-optimization/{id}/trial/{trial_id}/scenarios/

        Returns table data with input/output and per-evaluator scores for each dataset row.
        Matches the same format as simulation's trial_scenarios endpoint.
        """
        try:
            instance = self.get_object()
            trial = instance.trials.get(id=trial_id)

            header = self._get_trial_header_data(instance, trial)

            # Get trial items with their evaluations (similar to simulation pattern)
            trial_items = (
                trial.trial_items.all()
                .prefetch_related("evaluations__eval_metric__template")
                .order_by("row_id")
            )

            # Collect eval metrics to build dynamic columns
            eval_metrics = {}
            table_data = []

            for item in trial_items:
                # Parse input_text if it's JSON
                import json

                try:
                    input_data = json.loads(item.input_text) if item.input_text else {}
                except (json.JSONDecodeError, TypeError):
                    input_data = item.input_text or ""

                row = {
                    "id": str(item.id),
                    "input_text": input_data,
                    "output_text": item.output_text or "",
                }

                # Add per-evaluator scores as separate columns (like simulation)
                for evaluation in item.evaluations.all():
                    eval_metric_id = str(evaluation.eval_metric.id)
                    eval_name = (
                        evaluation.eval_metric.template.name
                        if evaluation.eval_metric.template
                        else f"Eval {eval_metric_id[:8]}"
                    )
                    eval_metrics[eval_metric_id] = eval_name
                    row[eval_metric_id] = (
                        round(evaluation.score, 4)
                        if evaluation.score is not None
                        else None
                    )

                table_data.append(row)

            # Fallback to metadata if no trial items exist yet (backwards compatibility)
            if (
                not table_data
                and trial.metadata
                and "individual_results" in trial.metadata
            ):
                individual_results = trial.metadata["individual_results"]
                for idx, result in enumerate(individual_results):
                    row = {
                        "id": result.get("row_id", str(idx)),
                        "input_text": result.get("input", {}),
                        "output_text": result.get("output", ""),
                    }
                    # Add individual eval scores if available
                    if "individual_scores" in result:
                        for i, (score, reason) in enumerate(
                            result["individual_scores"]
                        ):
                            eval_key = f"eval_{i}"
                            eval_metrics[eval_key] = f"Eval {i + 1}"
                            row[eval_key] = score
                    table_data.append(row)

            # Build dynamic eval columns (like simulation)
            eval_columns = [
                {"id": eval_id, "name": eval_name, "is_visible": True}
                for eval_id, eval_name in eval_metrics.items()
            ]

            # Column config: base columns + dynamic eval columns
            column_config = [
                {"id": "input_text", "name": "Input", "is_visible": True},
                {"id": "output_text", "name": "Output", "is_visible": True},
            ] + eval_columns

            return self._gm.success_response(
                {
                    **header,
                    "table": table_data,
                    "column_config": column_config,
                    "total_items": len(table_data),
                }
            )
        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DatasetOptimizationTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial scenarios: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))

    @action(
        detail=True,
        methods=["get"],
        url_path=r"trial/(?P<trial_id>[^/.]+)/evaluations",
    )
    def trial_evaluations(self, request, trial_id=None, *args, **kwargs):
        """
        Get evaluations for a trial grouped by eval_metric.

        URL: GET /dataset-optimization/{id}/trial/{trial_id}/evaluations/

        Returns table with average scores per evaluation metric.
        """
        try:
            instance = self.get_object()
            trial = instance.trials.get(id=trial_id)
            baseline_trial = instance.trials.filter(is_baseline=True).first()

            header = self._get_trial_header_data(instance, trial)

            # Get baseline eval scores for comparison
            baseline_eval_scores = {}
            if baseline_trial:
                baseline_evals = (
                    DatasetOptimizationItemEvaluation.objects.filter(
                        trial_item__trial=baseline_trial
                    )
                    .values("eval_metric__id")
                    .annotate(avg_score=Avg("score"))
                )
                for eval_data in baseline_evals:
                    baseline_eval_scores[eval_data["eval_metric__id"]] = eval_data[
                        "avg_score"
                    ]

            # Get trial evaluations grouped by eval_metric
            trial_evals = (
                DatasetOptimizationItemEvaluation.objects.filter(
                    trial_item__trial=trial
                )
                .values(
                    "eval_metric__id",
                    "eval_metric__template__name",
                    "eval_metric__template__description",
                )
                .annotate(avg_score=Avg("score"))
            )

            # Build table data
            table_data = []
            for eval_data in trial_evals:
                eval_metric_id = eval_data["eval_metric__id"]
                eval_score = eval_data["avg_score"]
                baseline_eval_score = baseline_eval_scores.get(eval_metric_id)

                percentage_change = calculate_percentage_point_change(
                    eval_score, baseline_eval_score
                )

                table_data.append(
                    {
                        "id": str(eval_metric_id),
                        "eval_name": eval_data["eval_metric__template__name"],
                        "eval_description": eval_data[
                            "eval_metric__template__description"
                        ],
                        "score": (
                            round(eval_score, 4) if eval_score is not None else None
                        ),
                        "score_percentage_change": percentage_change,
                    }
                )

            # Column config for the table
            column_config = [
                {"id": "eval_name", "name": "Evaluation", "is_visible": True},
                {"id": "eval_description", "name": "Description", "is_visible": True},
                {"id": "score", "name": "Avg Score", "is_visible": True},
                {
                    "id": "score_percentage_change",
                    "name": "% Change",
                    "is_visible": True,
                },
            ]

            return self._gm.success_response(
                {
                    **header,
                    "table": table_data,
                    "column_config": column_config,
                    "total_items": len(table_data),
                }
            )
        except Http404:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except DatasetOptimizationTrial.DoesNotExist:
            return self._gm.bad_request(get_error_message("PROMPT_TRIAL_NOT_FOUND"))
        except Exception as e:
            logger.exception(f"Error retrieving trial evaluations: {str(e)}")
            return self._gm.bad_request(get_error_message("FAILED_TO_FETCH_DATA"))
