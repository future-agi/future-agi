import re
import traceback
import uuid

import structlog
from django.forms import model_to_dict
from django.shortcuts import get_object_or_404
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = structlog.get_logger(__name__)
from model_hub.models.choices import ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable
from model_hub.models.run_prompt import RunPrompter
from model_hub.serializers.develop_dataset import DatasetSerializer
from model_hub.views.eval_runner import EvaluationRunner
from model_hub.views.utils.utils import get_recommendations, update_column_id
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.parse_errors import parse_serialized_errors
try:
    from ee.usage.models.usage import APICallStatusChoices, APICallTypeChoices
except ImportError:
    from tfc.oss_stubs.usage import APICallStatusChoices
    from tfc.oss_stubs.usage import APICallTypeChoices
try:
    from ee.usage.utils.usage_entries import log_and_deduct_cost_for_resource_request
except ImportError:
    from tfc.oss_stubs.usage import log_and_deduct_cost_for_resource_request


class CreateDatasetFromExpView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def is_valid_uuid4(self, s):
        try:
            val = uuid.UUID(s, version=4)
            # Ensure the string matches the canonical form
            return str(val) == s.lower()
        except (ValueError, AttributeError, TypeError):
            return False

    def post(self, request, exp_dataset_id, *args, **kwargs):
        try:
            new_dataset_name = request.data.get("name")
            model_type = request.data.get("model_type", ModelTypes.GENERATIVE_LLM.value)

            _org = getattr(request, "organization", None) or request.user.organization
            experiment_dataset = get_object_or_404(
                ExperimentDatasetTable,
                id=exp_dataset_id,
                deleted=False,
                experiments_datasets_created__dataset__organization=_org,
            )

            if not new_dataset_name:
                new_dataset_name = experiment_dataset.name

            call_log_row_entry = log_and_deduct_cost_for_resource_request(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                api_call_type=APICallTypeChoices.DATASET_ADD.value,
                workspace=request.workspace,
            )
            if (
                call_log_row_entry is None
                or call_log_row_entry.status
                == APICallStatusChoices.RESOURCE_LIMIT.value
            ):
                return self._gm.too_many_requests(
                    get_error_message("DATASET_CREATE_LIMIT_REACHED")
                )
            call_log_row_entry.status = APICallStatusChoices.SUCCESS.value
            call_log_row_entry.save()

            from model_hub.validators.dataset_validators import (
                validate_dataset_name_unique,
            )

            try:
                validate_dataset_name_unique(
                    new_dataset_name,
                    getattr(request, "organization", None) or request.user.organization,
                )
            except Exception as validation_err:
                return self._gm.bad_request(str(validation_err.detail[0]))

            dataset_serializer = DatasetSerializer(
                data={
                    "id": uuid.uuid4(),
                    "name": new_dataset_name,
                    "organization": (
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id,
                    "model_type": model_type,
                    "user": request.user.id,
                }
            )

            def handle_run_prompt(source_id, dataset):
                source_run_prompter = RunPrompter.objects.get(id=source_id)

                run_prompt_obj = RunPrompter.objects.create(
                    dataset=dataset,
                    **{
                        **model_to_dict(
                            source_run_prompter,
                            exclude=["id", "dataset", "organization", "tools"],
                        ),
                        "organization": getattr(request, "organization", None)
                        or request.user.organization,
                    },
                )

                run_prompt_obj.tools.set(source_run_prompter.tools.all())

                run_prompt_obj.save()
                return run_prompt_obj.id

            def handle_experiment_evaluation(source_id, dataset):
                try:
                    # Extract the actual UserEvalMetric ID from the source_id
                    if "-sourceid-" in str(source_id):
                        actual_id = str(source_id).split("-sourceid-")[1]
                    else:
                        actual_id = str(source_id)

                    # Validate that actual_id is a valid UUID
                    try:
                        uuid.UUID(actual_id)
                    except ValueError:
                        logger.error(
                            f"Invalid UUID format for actual_id {actual_id} from source_id {source_id}"
                        )
                        return None

                    # Check if the UserEvalMetric exists
                    try:
                        source_eval_metric = UserEvalMetric.objects.get(id=actual_id)
                    except UserEvalMetric.DoesNotExist:
                        logger.error(
                            f"UserEvalMetric with id {actual_id} does not exist for source_id {source_id}"
                        )
                        return None

                    # Create new UserEvalMetric
                    new_eval_metric = UserEvalMetric.objects.create(
                        dataset=dataset,
                        **{
                            **model_to_dict(
                                source_eval_metric,
                                exclude=[
                                    "id",
                                    "dataset",
                                    "organization",
                                    "config",
                                    "template",
                                ],
                            ),
                            "organization": getattr(request, "organization", None)
                            or request.user.organization,
                            "template": source_eval_metric.template,
                            "config": source_eval_metric.config,
                            "user": request.user,
                        },
                    )
                    return new_eval_metric.id
                except Exception as e:
                    logger.error(
                        f"Error handling experiment evaluation for source_id {source_id}: {str(e)}"
                    )
                    return None

            def handle_evaluation(source_id, dataset):
                try:
                    # Validate that source_id is a valid UUID
                    try:
                        uuid.UUID(str(source_id))
                    except ValueError:
                        logger.error(f"Invalid UUID format for source_id {source_id}")
                        return None

                    # Check if the UserEvalMetric exists
                    try:
                        source_eval_metric = UserEvalMetric.objects.get(id=source_id)
                    except UserEvalMetric.DoesNotExist:
                        logger.error(
                            f"UserEvalMetric with id {source_id} does not exist"
                        )
                        return None

                    # Create new UserEvalMetric
                    new_eval_metric = UserEvalMetric.objects.create(
                        dataset=dataset,
                        **{
                            **model_to_dict(
                                source_eval_metric,
                                exclude=[
                                    "id",
                                    "dataset",
                                    "organization",
                                    "config",
                                    "template",
                                ],
                            ),
                            "organization": getattr(request, "organization", None)
                            or request.user.organization,
                            "template": source_eval_metric.template,
                            "config": source_eval_metric.config,
                            "user": request.user,
                        },
                    )
                    return new_eval_metric.id
                except Exception as e:
                    logger.error(
                        f"Error handling evaluation for source_id {source_id}: {str(e)}"
                    )
                    return None

            source_handlers = {
                SourceChoices.RUN_PROMPT.value: handle_run_prompt,
                SourceChoices.EVALUATION.value: handle_evaluation,
                SourceChoices.EXPERIMENT_EVALUATION.value: handle_experiment_evaluation,
            }

            if not dataset_serializer.is_valid():
                return self._gm.bad_request(parse_serialized_errors(dataset_serializer))

            # experiment_columns = experiment_dataset.columns.all()
            columns_in_experiment = list(experiment_dataset.columns.all())
            experiment = experiment_dataset.experiment
            user_eval_metric = list(experiment.user_eval_template_ids.all())
            total_columns = []
            for metric in user_eval_metric:
                runner = EvaluationRunner(
                    user_eval_metric_id=metric.id,
                    is_only_eval=True,
                    format_output=True,
                    source_id=metric.template.id,
                    source="experiment",
                    source_configs={"dataset_id": str(experiment_dataset.id)},
                )
                user_eval = UserEvalMetric.objects.get(id=metric.id)
                cols_used = runner._get_all_column_ids_being_used(
                    user_eval_metric=user_eval
                )
                total_columns.extend([col_id for col_id in cols_used if col_id])

            total_columns.append(str(experiment.column.id))
            if experiment.column.source == SourceChoices.RUN_PROMPT.value:
                run_prompt = RunPrompter.objects.get(id=experiment.column.source_id)
                # Extract all column UUIDs from messages
                message_column_ids = []
                for message in run_prompt.messages:
                    content = message.get("content", None)

                    if isinstance(content, list):
                        for item in content:
                            if item.get("type") == "text":
                                content = item.get("text", "")
                                column_ids = re.findall(r"\{\{([^}]*)\}\}", content)
                                message_column_ids.extend(
                                    [col_id.strip() for col_id in column_ids if col_id]
                                )

                    elif isinstance(content, str):
                        # Find all UUIDs between {{ and }}
                        column_ids = re.findall(r"\{\{([^}]*)\}\}", content)
                        # Clean up any whitespace and add to list
                        message_column_ids.extend(
                            [col_id.strip() for col_id in column_ids if col_id]
                        )

                # Add unique column IDs to total_columns
                total_columns.extend(
                    [col_id for col_id in message_column_ids if col_id]
                )

            valid_total_columns = [s for s in total_columns if self.is_valid_uuid4(s)]

            columns_in_datasets = list(
                Column.objects.filter(
                    id__in=valid_total_columns,  # Convert UUIDs to strings
                    deleted=False,
                    dataset=experiment.dataset,
                ).order_by("created_at")
            )
            experiment_columns = columns_in_experiment + columns_in_datasets
            experiment_columns = [
                experiment_column
                for experiment_column in experiment_columns
                if not experiment_column.name.endswith("reason")
            ]
            column_id_mapping = {}
            row_id_mapping = {}
            row_order = 0
            column_order = []
            column_config = {}

            try:
                new_dataset = dataset_serializer.save()
            except Exception:
                return self._gm.bad_request(
                    get_error_message("FAILED_TO_CREATE_DATASET_FROM_EXP")
                )

            for column in experiment_columns:
                new_column_id = uuid.uuid4()
                column_id_mapping[str(column.id)] = str(new_column_id)

                # Handle source_id with proper error handling
                source_id = None
                if column.source in [
                    SourceChoices.RUN_PROMPT.value,
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                ]:
                    try:
                        source_id = source_handlers[column.source](
                            column.source_id, new_dataset
                        )
                        if source_id is None:
                            logger.warning(
                                f"Source handler returned None for column {column.name} with source {column.source} and source_id {column.source_id}"
                            )
                    except Exception as e:
                        logger.error(
                            f"Error calling source handler for column {column.name}: {str(e)}"
                        )
                        source_id = None

                new_column = Column.objects.create(
                    id=new_column_id,
                    dataset=new_dataset,
                    source=(
                        column.source
                        if column.source
                        not in [SourceChoices.EXPERIMENT_EVALUATION.value]
                        else SourceChoices.EVALUATION.value
                    ),
                    source_id=source_id,
                    **{
                        k: v
                        for k, v in model_to_dict(column).items()
                        if k not in ["id", "dataset", "source_id", "source"]
                    },
                )
                column_order.append(str(new_column.id))
                column_config[str(column.id)] = {"is_visible": True, "is_frozen": None}

                logger.info(f"Created column: {column.name}")

            for column in experiment_columns:
                if column.source == SourceChoices.RUN_PROMPT.value:
                    new_column = Column.objects.get(
                        id=column_id_mapping[str(column.id)]
                    )
                    run_prompt_col = RunPrompter.objects.get(id=new_column.source_id)
                    new_messages = []
                    for message in run_prompt_col.messages:
                        new_messages.append(
                            update_column_id(
                                message=message, column_mapping=column_id_mapping
                            )
                        )
                    run_prompt_col.messages = new_messages
                    run_prompt_col.save()

                elif column.source in [
                    SourceChoices.EVALUATION.value,
                    SourceChoices.EXPERIMENT_EVALUATION.value,
                ]:
                    new_col = Column.objects.get(id=column_id_mapping[str(column.id)])
                    if new_col.source_id is None:
                        logger.warning(
                            f"Skipping column {column.name} because source_id is None"
                        )
                        continue
                    try:
                        user_eval_col = UserEvalMetric.objects.get(id=new_col.source_id)
                    except UserEvalMetric.DoesNotExist:
                        logger.error(
                            f"UserEvalMetric with id {new_col.source_id} does not exist for column {column.name}"
                        )
                        continue
                    config = user_eval_col.config
                    new_mapping_column_ids = {}
                    mapping_column_ids_initial = config.get("mapping", {})
                    for key in mapping_column_ids_initial.keys():
                        vals = mapping_column_ids_initial[key]
                        if isinstance(vals, list):
                            new_vals = []
                            for col_id in vals:
                                new_col_id = column_id_mapping[str(col_id)]
                                new_vals.append(new_col_id)
                        else:
                            new_vals = column_id_mapping[str(vals)]

                        new_mapping_column_ids.update({key: new_vals})

                    deterministic_column_ids = config.get("config", {}).get("input", [])
                    deterministic_column_ids = [
                        column_id.strip() for column_id in deterministic_column_ids
                    ]
                    deterministic_column_ids = [
                        column_id.replace("{{", "").replace("}}", "")
                        for column_id in deterministic_column_ids
                    ]
                    new_deteministic_column_ids = []
                    for det_column_id in deterministic_column_ids:
                        new_deteministic_column_ids.append(
                            "{{" + column_id_mapping[str(det_column_id)] + "}}"
                        )

                    if new_mapping_column_ids:
                        config["mapping"] = new_mapping_column_ids

                    if deterministic_column_ids:
                        config["input"] = new_deteministic_column_ids

                    user_eval_col.config = config
                    user_eval_col.save()

            for column in experiment_columns:
                for cell in column.cell_set.filter(
                    deleted=False, row__deleted=False, column__deleted=False
                ):
                    if cell.row.deleted is False and cell.column.deleted is False:
                        if str(cell.row.id) not in row_id_mapping:
                            new_row_id = uuid.uuid4()
                            row_id_mapping[str(cell.row.id)] = str(new_row_id)
                            Row.objects.create(
                                id=new_row_id, dataset=new_dataset, order=row_order
                            )
                            row_order += 1

                        Cell.objects.create(
                            id=uuid.uuid4(),
                            row_id=row_id_mapping[str(cell.row.id)],
                            column_id=column_id_mapping[str(cell.column.id)],
                            dataset=new_dataset,
                            **{
                                k: v
                                for k, v in model_to_dict(cell).items()
                                if k not in ["id", "row", "column", "dataset"]
                            },
                        )

            get_recommendations(new_dataset)
            new_dataset.column_order = column_order
            new_dataset.column_config = column_config
            new_dataset.save(update_fields=["column_order", "column_config"])
            return self._gm.success_response(f"{new_dataset.name} has been created.")

        except Exception as e:
            logger.error(traceback.format_exc())
            traceback.print_exc()
            logger.exception(f"Error in creating the dataset from experiment: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_DATASET_FROM_EXP")
            )
