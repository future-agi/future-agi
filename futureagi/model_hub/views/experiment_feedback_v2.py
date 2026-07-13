import structlog
from django.core.exceptions import ValidationError
from django.db.models import Q
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from evaluations.constants import FUTUREAGI_EVAL_TYPES
from model_hub.models.choices import CellStatus, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import Feedback, UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ExperimentFeedbackSubmitRequestSerializer,
)
from model_hub.serializers.develop_dataset import FeedbackSerializer
from model_hub.serializers.experiment_contracts import (
    ExperimentFeedbackCreateResponseSerializer,
    ExperimentFeedbackDetailsResponseSerializer,
    ExperimentFeedbackSubmitResponseSerializer,
    ExperimentFeedbackTemplateResponseSerializer,
)
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.utils.constants import EVAL_OUTPUT_TYPES
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _get_experiment_or_error(experiment_id, organization, gm):
    """Validate experiment exists, belongs to org, and has a snapshot dataset."""
    experiment = ExperimentsTable.objects.filter(
        id=experiment_id,
        dataset__organization=organization,
        deleted=False,
    ).first()
    if not experiment:
        return None, gm.bad_request("Experiment not found.")
    if not experiment.snapshot_dataset_id:
        return None, gm.bad_request("Experiment has no snapshot dataset.")
    return experiment, None


def _get_experiment_metric_or_error(
    experiment, organization, user_eval_metric_id, gm
):
    """Validate the metric belongs to the organization and this experiment."""
    try:
        user_eval_metric = experiment.user_eval_template_ids.select_related(
            "template"
        ).get(
            id=user_eval_metric_id,
            organization=organization,
            deleted=False,
        )
    except (UserEvalMetric.DoesNotExist, ValidationError):
        return None, gm.bad_request(get_error_message("MISSING_USER_EVAL_METRIC_ID"))

    return user_eval_metric, None


def _column_matches_metric(column, user_eval_metric):
    source_id = str(column.source_id or "")
    metric_id = str(user_eval_metric.id)
    return source_id == metric_id or source_id.endswith(f"-sourceid-{metric_id}")


def _get_feedback_column_or_error(experiment, source_id, user_eval_metric, gm):
    try:
        column = Column.objects.get(
            id=source_id,
            dataset_id=experiment.snapshot_dataset_id,
            deleted=False,
        )
    except (Column.DoesNotExist, ValidationError):
        return None, gm.bad_request(get_error_message("FAILED_TO_CREATE_FEEDBACK"))

    if column.source not in {
        SourceChoices.EXPERIMENT_EVALUATION.value,
        SourceChoices.EVALUATION.value,
    } or not _column_matches_metric(column, user_eval_metric):
        return None, gm.bad_request(get_error_message("FAILED_TO_CREATE_FEEDBACK"))

    return column, None


class ExperimentFeedbackGetTemplateV2View(APIView):
    """Get evaluation template details for rendering the feedback form."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: ExperimentFeedbackTemplateResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, experiment_id):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            experiment, err = _get_experiment_or_error(
                experiment_id, organization, self._gm
            )
            if err:
                return err

            user_eval_metric_id = request.query_params.get("user_eval_metric_id")
            if not user_eval_metric_id:
                return self._gm.bad_request(
                    get_error_message("USER_EVAL_METRIC_ID_REQUIRED")
                )

            user_eval_metric, err = _get_experiment_metric_or_error(
                experiment, organization, user_eval_metric_id, self._gm
            )
            if err:
                return err

            eval_template = user_eval_metric.template
            if not eval_template:
                return self._gm.not_found(get_error_message("EVAL_TEMP_NOT_FOUND"))

            template_data = {
                "output_type": eval_template.config.get("output"),
                "eval_description": eval_template.description,
                "eval_name": eval_template.name,
                "user_eval_name": user_eval_metric.name,
                "choice_scores": eval_template.choice_scores or None,
            }

            if template_data["output_type"] == EVAL_OUTPUT_TYPES["PASS_FAIL"]:
                template_data["choices"] = ["Passed", "Failed"]

            elif template_data["output_type"] == EVAL_OUTPUT_TYPES["CHOICES"]:
                if (
                    user_eval_metric.config
                    and isinstance(user_eval_metric.config, dict)
                    and "config" in user_eval_metric.config
                    and "choices" in user_eval_metric.config["config"]
                    and user_eval_metric.config["config"]["choices"]
                ):
                    template_data["choices"] = user_eval_metric.config["config"][
                        "choices"
                    ]
                    template_data["multi_choice"] = user_eval_metric.config[
                        "config"
                    ].get("multi_choice", False)

                elif hasattr(eval_template, "choices") and eval_template.choices:
                    template_data["choices"] = eval_template.choices
                    template_data["multi_choice"] = eval_template.config.get(
                        "multi_choice", False
                    )

                else:
                    template_data["choices"] = []
                    template_data["multi_choice"] = False

            return self._gm.success_response(template_data)

        except Exception as e:
            logger.exception(f"Error fetching feedback template: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_USER_EVAL_METRIC")
            )


class ExperimentFeedbackCreateV2View(APIView):
    """Create a feedback record scoped to an experiment."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=FeedbackSerializer,
        responses={200: ExperimentFeedbackCreateResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, experiment_id):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            experiment, err = _get_experiment_or_error(
                experiment_id, organization, self._gm
            )
            if err:
                return err

            serializer = request.validated_serializer
            data = serializer.validated_data
            if data.get("source") != "experiment":
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_CREATE_FEEDBACK")
                )

            source_id = data.get("source_id")
            row_id = data.get("row_id")
            submitted_metric = data.get("user_eval_metric")
            if not submitted_metric:
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_CREATE_FEEDBACK")
                )
            user_eval_metric, err = _get_experiment_metric_or_error(
                experiment, organization, submitted_metric.id, self._gm
            )
            if err:
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_CREATE_FEEDBACK")
                )

            _, err = _get_feedback_column_or_error(
                experiment, source_id, user_eval_metric, self._gm
            )
            if err:
                return err
            if row_id and not Row.objects.filter(
                id=row_id,
                dataset_id=experiment.snapshot_dataset_id,
                deleted=False,
            ).exists():
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_CREATE_FEEDBACK")
                )

            feedback = serializer.save(
                user=request.user,
                organization=organization,
                workspace=getattr(request, "workspace", None),
            )

            return self._gm.success_response({"id": feedback.id})

        except (ValidationError, DRFValidationError):
            return self._gm.bad_request(get_error_message("FAILED_TO_CREATE_FEEDBACK"))
        except Exception as e:
            logger.exception(f"Error creating experiment feedback: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_FEEDBACK")
            )


class ExperimentFeedbackDetailsV2View(APIView):
    """Get previous feedback details for a metric+row in an experiment."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: ExperimentFeedbackDetailsResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, experiment_id):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            experiment, err = _get_experiment_or_error(
                experiment_id, organization, self._gm
            )
            if err:
                return err

            user_eval_metric_id = request.query_params.get("user_eval_metric_id")
            row_id = request.query_params.get("row_id")

            experiment_columns = list(
                Column.objects.filter(
                    dataset_id=experiment.snapshot_dataset_id,
                    deleted=False,
                    source__in=[
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EVALUATION.value,
                    ],
                )
            )
            if user_eval_metric_id:
                user_eval_metric, err = _get_experiment_metric_or_error(
                    experiment, organization, user_eval_metric_id, self._gm
                )
                if err:
                    return self._gm.success_response(
                        {"feedback": [], "total_count": 0}
                    )
                experiment_columns = [
                    column
                    for column in experiment_columns
                    if _column_matches_metric(column, user_eval_metric)
                ]

            queryset = Feedback.objects.select_related("user").filter(
                deleted=False,
                organization=organization,
                source="experiment",
                source_id__in=[str(column.id) for column in experiment_columns],
            )

            if user_eval_metric_id:
                queryset = queryset.filter(user_eval_metric_id=user_eval_metric_id)
            if row_id:
                queryset = queryset.filter(row_id=row_id)

            queryset = queryset.order_by("-created_at")

            feedback_data = []
            for feedback in queryset:
                feedback_data.append(
                    {
                        "id": str(feedback.id),
                        "value": feedback.value,
                        "comment": feedback.explanation,
                        "created_at": feedback.created_at.isoformat(),
                        "action_type": feedback.action_type,
                    }
                )

            return self._gm.success_response(
                {"feedback": feedback_data, "total_count": len(feedback_data)}
            )

        except Exception as e:
            logger.exception(f"Error fetching experiment feedback details: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_FEEDBACKS")
            )


class ExperimentFeedbackSubmitV2View(APIView):
    """Submit feedback action — triggers temporal eval rerun for experiments."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=ExperimentFeedbackSubmitRequestSerializer,
        responses={200: ExperimentFeedbackSubmitResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, experiment_id):
        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            experiment, err = _get_experiment_or_error(
                experiment_id, organization, self._gm
            )
            if err:
                return err

            data = request.validated_data
            action_type = data.get("action_type")
            feedback_id = data.get("feedback_id")
            user_eval_metric_id = data.get("user_eval_metric_id")
            value = data.get("value") if data.get("value") else None
            explanation = data.get("explanation") if data.get("explanation") else None

            if not action_type or not user_eval_metric_id or not feedback_id:
                return self._gm.bad_request(
                    get_error_message("MISSING_METRIC_ID_FEEDBACK_ID_AND_ACTION_TYPE")
                )

            valid_actions = [
                "retune",
                "recalculate_row",
                "recalculate_dataset",
                "retune_recalculate",
            ]
            if action_type not in valid_actions:
                return self._gm.bad_request(
                    f"Invalid action_type. Must be one of: {', '.join(valid_actions)}"
                )

            # Load feedback
            feedback = Feedback.objects.get(
                id=feedback_id,
                organization=organization,
                source="experiment",
                deleted=False,
            )
            feedback.action_type = action_type

            row_id = str(feedback.row_id)

            # Load eval column and dataset from snapshot
            snapshot_dataset_id = str(experiment.snapshot_dataset_id)
            user_eval_metric, err = _get_experiment_metric_or_error(
                experiment, organization, user_eval_metric_id, self._gm
            )
            if err:
                return err
            if feedback.user_eval_metric_id != user_eval_metric.id:
                return self._gm.bad_request(
                    get_error_message("MISSING_USER_EVAL_METRIC_ID")
                )
            eval_column, err = _get_feedback_column_or_error(
                experiment, feedback.source_id, user_eval_metric, self._gm
            )
            if err:
                return self._gm.bad_request("Evaluation column not found.")
            if feedback.row_id and not Row.objects.filter(
                id=feedback.row_id,
                dataset_id=snapshot_dataset_id,
                deleted=False,
            ).exists():
                return self._gm.bad_request("Feedback not found.")

            feedback.eval_template = user_eval_metric.template
            feedback.value = value if value else feedback.value
            feedback.explanation = explanation if explanation else feedback.explanation
            feedback.save()

            # Build row_dict for embedding
            row_cells = Cell.objects.filter(
                row_id=feedback.row_id,
                dataset_id=snapshot_dataset_id,
                deleted=False,
            ).select_related("column")

            row_dict = {}
            for cell in row_cells:
                column_id = str(cell.column.id)
                if column_id != str(eval_column.id):
                    row_dict[column_id] = cell.value
                    if cell.column.name:
                        row_dict[cell.column.name] = cell.value

            row_dict["feedback_comment"] = feedback.explanation
            row_dict["feedback_value"] = feedback.value

            # Embed feedback for RAG few-shot
            futureagi_eval = (
                user_eval_metric.template.config.get("eval_type_id")
                in FUTUREAGI_EVAL_TYPES
            )
            runner = EvaluationRunner(
                user_eval_metric.template.config.get("eval_type_id"),
                format_output=True,
                futureagi_eval=futureagi_eval,
            )

            required_field, mapping = runner._get_required_fields_and_mappings(
                user_eval_metric=user_eval_metric
            )
            embedding_manager = EmbeddingManager()
            embedding_manager.parallel_process_metadata(
                eval_id=user_eval_metric.template.id,
                metadatas=row_dict,
                inputs_formater=required_field,
                organization_id=organization.id,
                workspace_id=(
                    experiment.dataset.workspace.id
                    if experiment.dataset.workspace
                    else None
                ),
            )
            embedding_manager.close()

            # Handle actions
            if action_type == "retune":
                return self._gm.success_response(
                    {
                        "message": "Metric queued for retuning",
                        "action_type": action_type,
                        "user_eval_metric_id": str(user_eval_metric_id),
                    }
                )

            # For recalculate actions, determine affected columns and trigger temporal
            recalculate_row_only = action_type in (
                "recalculate_row",
                "retune_recalculate",
            )
            row_ids = [row_id] if recalculate_row_only else []

            workflow_id = self._trigger_eval_rerun(
                experiment=experiment,
                eval_column=eval_column,
                snapshot_dataset_id=snapshot_dataset_id,
                row_ids=row_ids,
            )

            message = (
                "Row queued for recalculation"
                if recalculate_row_only
                else "Dataset queued for recalculation"
            )

            return self._gm.success_response(
                {
                    "message": message,
                    "action_type": action_type,
                    "user_eval_metric_id": str(user_eval_metric_id),
                    "workflow_id": workflow_id,
                }
            )

        except Feedback.DoesNotExist:
            return self._gm.bad_request("Feedback not found.")
        except Column.DoesNotExist:
            return self._gm.bad_request("Evaluation column not found.")
        except Exception as e:
            logger.exception(f"Error submitting experiment feedback: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_FEEDBACK")
            )

    def _trigger_eval_rerun(
        self, experiment, eval_column, snapshot_dataset_id, row_ids
    ):
        """Identify affected eval columns, reset state, and trigger temporal workflow."""
        from tfc.temporal.experiments import start_rerun_cells_v2_workflow

        source = eval_column.source
        source_id = eval_column.source_id

        if source in (
            SourceChoices.EXPERIMENT_EVALUATION.value,
            SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
        ):
            # Per-EDT eval: source_id = "{edt_id}-{col_id}-sourceid-{metric_id}"
            left, metric_id = source_id.split("-sourceid-")
            edt_id = left[:36]

            # Find all eval columns for this EDT + metric
            eval_column_ids = list(
                Column.objects.filter(
                    source_id__startswith=edt_id,
                    source_id__endswith=f"-sourceid-{metric_id}",
                    source__in=[
                        SourceChoices.EXPERIMENT_EVALUATION.value,
                        SourceChoices.EXPERIMENT_EVALUATION_TAGS.value,
                    ],
                    dataset_id=snapshot_dataset_id,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            # Reset columns and cells
            self._reset_state(eval_column_ids, snapshot_dataset_id, row_ids, experiment)

            workflow_id = start_rerun_cells_v2_workflow(
                experiment_id=str(experiment.id),
                dataset_id=snapshot_dataset_id,
                prompt_config_ids=[],
                agent_config_ids=[],
                row_ids=row_ids,
                eval_template_ids=[metric_id],
                eval_only=True,
                edt_ids=[edt_id],
            )

        elif source in (
            SourceChoices.EVALUATION.value,
            SourceChoices.EVALUATION_TAGS.value,
        ):
            # Base eval: source_id = str(metric_id)
            metric_id = source_id

            eval_column_ids = list(
                Column.objects.filter(
                    source_id=metric_id,
                    source__in=[
                        SourceChoices.EVALUATION.value,
                        SourceChoices.EVALUATION_TAGS.value,
                    ],
                    dataset_id=snapshot_dataset_id,
                    deleted=False,
                ).values_list("id", flat=True)
            )

            self._reset_state(eval_column_ids, snapshot_dataset_id, row_ids, experiment)

            workflow_id = start_rerun_cells_v2_workflow(
                experiment_id=str(experiment.id),
                dataset_id=snapshot_dataset_id,
                prompt_config_ids=[],
                agent_config_ids=[],
                row_ids=row_ids,
                eval_template_ids=[metric_id],
                eval_only=True,
                base_eval_only=True,
            )

        else:
            raise ValueError(f"Unsupported column source for feedback rerun: {source}")

        return workflow_id

    def _reset_state(self, eval_column_ids, snapshot_dataset_id, row_ids, experiment):
        """Reset columns and cells to RUNNING before triggering workflow."""
        Column.objects.filter(id__in=eval_column_ids).update(
            status=StatusType.RUNNING.value
        )

        cell_filter = Q(
            column_id__in=eval_column_ids,
            dataset_id=snapshot_dataset_id,
        )
        if row_ids:
            cell_filter &= Q(row_id__in=row_ids)

        Cell.objects.filter(cell_filter).update(
            status=CellStatus.RUNNING.value, value=""
        )

        experiment.status = StatusType.RUNNING.value
        experiment.save(update_fields=["status"])
