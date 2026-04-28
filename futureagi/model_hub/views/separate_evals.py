import json
import math
import traceback
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import structlog
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models import OrgApiKey
from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from model_hub.constants import (
    EVAL_PLAYGROUND_CURL_CODE,
    EVAL_PLAYGROUND_JS_CODE,
    EVAL_PLAYGROUND_PYTHON_CODE,
)
from model_hub.models.choices import EvalOutputType, EvalTemplateType
from model_hub.models.develop_dataset import SourceChoices
from model_hub.models.evals_metric import (
    EvalSettings,
    EvalTemplate,
    Feedback,
    OwnerChoices,
    UserEvalMetric,
)
from model_hub.models.run_prompt import PromptEvalConfig
from model_hub.serializers.develop_dataset import (
    EvalPlayGroundFeedbackSerializer,
)
from model_hub.serializers.eval_runner import (
    DeleteEvalTemplateSerializer,
    DuplicateEvalTemplateSerializer,
    EvalPlayGroundSerializer,
    TestEvalTemplateSerializer,
    UpdateColumnConfigSerializer,
    UpdateEvalTemplateSerializer,
)
from model_hub.utils.eval_playground_call_context import (
    build_eval_playground_scenario_context,
)
from model_hub.utils.evals import prepare_user_eval_config
from model_hub.utils.function_eval_params import (
    has_function_params_schema,
    normalize_eval_runtime_config,
)
from model_hub.utils.SQL_queries import SQLQueryHandler
from model_hub.views.utils.evals import run_eval_func, run_eval_func_task
from tfc.settings.settings import BASE_URL
from tfc.telemetry import wrap_for_thread
from tfc.utils.error_codes import get_error_message
from tfc.utils.functions import calculate_eval_average
from tfc.utils.general_methods import GeneralMethods
try:
    from ee.usage.exceptions import UsageLimitExceeded
except ImportError:
    UsageLimitExceeded = None

logger = structlog.get_logger(__name__)
from tracer.models.custom_eval_config import CustomEvalConfig, InlineEval, ModelChoices
from tracer.models.external_eval_config import ExternalEvalConfig
from tracer.models.observation_span import EvalLogger
from tracer.utils.filters import apply_created_at_filters
from tracer.utils.graphs import GraphEngine
try:
    from ee.usage.models.usage import APICallLog, APICallStatusChoices
except ImportError:
    APICallLog = None
    APICallStatusChoices = None


def apply_filters(row_data, filters):
    filtered_data = row_data

    for filter_item in filters:
        try:
            column_id = filter_item.get("column_id")
            filter_config = filter_item.get("filter_config", {})

            if not column_id or not filter_config:
                continue

            filter_type = filter_config.get("filter_type")
            filter_op = filter_config.get("filter_op")
            filter_value = filter_config.get("filter_value")

            if filter_value is None:
                continue

            if filter_type == "text":
                filter_value = filter_value.lower()
                text_ops = {
                    "contains": lambda x, fv=filter_value: fv in x.lower(),
                    "not_contains": lambda x, fv=filter_value: fv not in x.lower(),
                    "equals": lambda x, fv=filter_value: x.lower() == fv,
                    "not_equals": lambda x, fv=filter_value: x.lower() != fv,
                    "starts_with": lambda x, fv=filter_value: x.lower().startswith(fv),
                    "ends_with": lambda x, fv=filter_value: x.lower().endswith(fv),
                    "in": lambda x, fv=filter_value: x.lower() in fv,
                    "not_in": lambda x, fv=filter_value: x.lower() not in fv,
                }

                if filter_op not in text_ops:
                    message = (
                        "Invalid filter operation. \
                        Allowed operations are: "
                        + ", ".join(text_ops.keys())
                    )
                    raise ValueError(message)

                result = []

                for row in filtered_data:
                    if row.get(column_id, None) is None:
                        continue
                    value = row[column_id]["cell_value"]

                    if isinstance(value, dict) and "output" in value:
                        value = value["output"]

                    if value is None:
                        continue

                    if not isinstance(value, str):
                        value = str(value)

                    if text_ops[filter_op](value):
                        result.append(row)

                filtered_data = result

            elif filter_type == "number":
                operator_map = {
                    "greater_than": lambda x, y: x > y,
                    "less_than": lambda x, y: x < y,
                    "equals": lambda x, y: x == y,
                    "not_equals": lambda x, y: x != y,
                    "greater_than_or_equal": lambda x, y: x >= y,
                    "less_than_or_equal": lambda x, y: x <= y,
                    "between": lambda x, y: y[0] <= x <= y[1],
                    "not_in_between": lambda x, y: x < y[0] or x > y[1],
                }
                result = []
                if filter_op in operator_map:
                    if not isinstance(filter_value, float) and not isinstance(
                        filter_value, list
                    ):
                        filter_value = float(filter_value)

                    for row in filtered_data:
                        if row.get(column_id, None) is None:
                            continue
                        value = row[column_id]["cell_value"]
                        if isinstance(value, dict) and "output" in value:
                            value = value["output"]

                        if value is None:
                            continue

                        if not isinstance(value, float):
                            value = float(value)

                        value = round(value * 100, 2)

                        if operator_map[filter_op](value, filter_value):
                            result.append(row)

                filtered_data = result

            elif filter_type == "boolean":
                result = []
                if filter_value not in ["true", "false", "passed", "failed"]:
                    raise ValueError(
                        "Invalid filter value. Allowed values are: true, false"
                    )

                for row in filtered_data:
                    if row.get(column_id, None) is None:
                        continue
                    value = row[column_id]["cell_value"]

                    if isinstance(value, dict) and "output" in value:
                        value = value["output"]

                    if value is None:
                        continue

                    if not isinstance(value, str):
                        value = str(value)

                    value = value.lower()

                    if (filter_value == "true" or filter_value == "passed") and (
                        value == "true" or value == "passed"
                    ):
                        result.append(row)
                    elif (filter_value == "false" or filter_value == "failed") and (
                        value == "false" or value == "failed"
                    ):
                        result.append(row)
                    else:
                        continue

                filtered_data = result

            else:
                message = (
                    "Invalid filter type. "
                    "Allowed types are: text, number, boolean, datetime"
                )
                raise ValueError(message)

        except Exception as e:
            logger.error(f"error in filter : {e}")
            raise e

    return filtered_data


def get_eval_metric_data(eval_template, filters, logs, error=False):
    if not eval_template:
        raise Exception("EvalTemplate not found")

    query = Q()
    if filters:
        filter_config = filters[0].get("filterConfig") or filters[0].get(
            "filter_config"
        )
        start_date, end_date = filter_config.get(
            "filterValue", []
        ) or filter_config.get("filter_value", [])

        if start_date:
            query &= Q(created_at__gte=start_date)
        if end_date:
            query &= Q(created_at__lte=end_date)

    api_logs = logs.filter(query)

    api_call_count = api_logs.count()

    if api_call_count != 0:
        average = calculate_eval_average(eval_template, api_logs)
    else:
        average = 0

    graph_engine = GraphEngine(
        objects=api_logs,
        interval="day",
        filters=filters,
        observe_type="eval_metric",
        error=error,
    )
    graph_data = graph_engine.generate_graph(
        metric="eval_metric", eval_template=eval_template
    )

    response_data = {
        "base_eval_template_id": eval_template.id,
        "api_call_count": {
            "api_call_count": api_call_count,
            "count_graph_data": graph_data.get("count_graph_data"),
        },
        "average": {
            "average": average,
            "avg_graph_data": graph_data.get("avg_graph_data"),
        },
    }
    if error:
        response_data.update({"error_rate": graph_data.get("error_rate")})

    return response_data

    unique_log_days = set({entry["log_date"].date() for entry in logs})

    return len(unique_log_days)


class GetAPICallLogDetailsView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            eval_template_id = request.query_params.get(
                "eval_template_id", None
            ) or request.query_params.get("evalTemplateId", None)
            page_size = int(request.query_params.get("page_size", 10)) or int(
                request.query_params.get("pageSize", 10)
            )
            current_page = int(
                request.query_params.get("current_page_index", 0)
            ) or int(request.query_params.get("currentPageIndex", 0))
            source = request.query_params.get(
                "source", "logs"
            ) or request.query_params.get("source", "logs")
            search = request.query_params.get("search", "") or request.query_params.get(
                "search", ""
            )

            if not eval_template_id:
                return self._gm.bad_request({"error": "No eval template id provided"})

            logs = APICallLog.objects.filter(
                source_id=eval_template_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                status__in=[
                    APICallStatusChoices.SUCCESS.value,
                    APICallStatusChoices.ERROR.value,
                ],
                deleted=False,
            ).order_by("-created_at")

            if source == "feedback":
                logs = logs.filter(source="feedback")

            if source == "eval_playground":
                logs = logs.filter(source="eval_playground")

            column_data = get_column_data(eval_template_id, source, request.user)

            # Parse filters from query params (sent as JSON string)
            filters_param = request.query_params.get("filters", "[]")
            try:
                filters = json.loads(filters_param)
                if not isinstance(filters, list):
                    filters = []
            except (json.JSONDecodeError, TypeError):
                filters = []
            if filters:
                logs, new_filters = apply_created_at_filters(logs, filters)
            else:
                new_filters = []

            if not logs.exists():
                return self._gm.success_response(
                    {"table": [], "column_config": column_data}
                )

            eval_template = EvalTemplate.no_workspace_objects.get(
                id=eval_template_id, deleted=False
            )
            key_map = {col.get("id"): col.get("name") for col in column_data}
            table_data = {}
            table_data["column_config"] = column_data
            row_data = []

            # Wrap function with OTel context propagation for thread safety
            wrapped_populate_log_row_data = wrap_for_thread(populate_log_row_data)

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = []
                for batch in batch_queryset(logs, 10):
                    future = executor.submit(
                        wrapped_populate_log_row_data, eval_template, batch, key_map
                    )
                    futures.append(future)

                # Preserve original batch order by iterating futures directly
                # instead of using as_completed() which returns in completion order
                for future in futures:
                    row_data.extend(future.result())

            if new_filters:
                row_data = apply_filters(row_data, new_filters)

            # Parse sort from query params (sent as JSON string)
            sort_param = request.query_params.get("sort", "[]")
            try:
                sort_config = json.loads(sort_param)
                if not isinstance(sort_config, list):
                    sort_config = []
            except (json.JSONDecodeError, TypeError):
                sort_config = []
            if sort_config and row_data and len(row_data) > 0:
                for sort_item in sort_config:
                    column_id = sort_item.get("column_id")
                    sort_type = sort_item.get("type")
                    reverse = sort_type == "descending"

                    def get_sort_key(item, col_id=column_id):
                        if not col_id:
                            return (
                                ""  # Default return value if column_id is not provided.
                            )

                        try:
                            # If column_id is not nested, fetch the value directly
                            value = item.get(col_id, {}).get("cell_value", "")
                            if not isinstance(value, str):
                                value = str(value)

                            return (
                                str(value).lower()
                                if isinstance(value, str)
                                else (value or 0)
                            )

                        except (AttributeError, TypeError):
                            # If we can't get the value, return a default empty string
                            return ""

                    row_data.sort(key=get_sort_key, reverse=reverse)

            if search:
                row_data = apply_search(row_data, search, column_data)

            total_rows = len(row_data) if row_data is not None else 0
            start = current_page * page_size
            end = start + page_size

            table_data["table"] = row_data[start:end] if row_data is not None else []
            metadata = {}
            metadata["total_rows"] = total_rows
            metadata["total_pages"] = (total_rows + page_size - 1) // page_size
            table_data["metadata"] = metadata

            return self._gm.success_response(table_data)

        except Exception as e:
            logger.exception(f"Error in GetAPICallLogs: {str(e)}")
            return self._gm.internal_server_error_response(str(e))


class GetAPICallLogView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            log_id = request.query_params.get("log_id", None)
            try:
                log_row = APICallLog.objects.get(log_id=log_id)
            except APICallLog.DoesNotExist:
                return self._gm.bad_request(
                    get_error_message("LOG_ROW_FETCHING_FAILED")
                )
            row_data = {}

            config = json.loads(log_row.config)
            error_localizer = config.get("error_localizer", {})
            # Look up the ErrorLocalizerTask keyed by this log_id so the
            # frontend can distinguish "still running" from "never started"
            # and from "completed". The task row is populated by
            # `trigger_error_localization_for_playground` when the playground
            # is called with error_localizer=true.
            error_localizer_status = None
            error_localizer_message = None
            try:
                from model_hub.models.error_localizer_model import (
                    ErrorLocalizerTask,
                )

                task = ErrorLocalizerTask.objects.filter(
                    source_id=log_row.log_id
                ).first()
                if task:
                    error_localizer_status = task.status
                    error_localizer_message = task.error_message
                    # If the task finished but the APICallLog.config hasn't
                    # been patched yet (or the localizer failed), surface
                    # the structured result directly from the task row.
                    if (
                        not error_localizer
                        and task.status == "completed"
                        and task.error_analysis
                    ):
                        error_localizer = {
                            "error_analysis": task.error_analysis,
                            "selected_input_key": task.selected_input_key,
                            "input_types": task.input_types,
                            "input_data": task.input_data,
                        }
            except Exception:
                logger.exception("Failed to look up ErrorLocalizerTask")
            log_source = config.get("source", None) or log_row.source
            log_source = log_source.replace("_", " ").title() if log_source else None

            required_keys = config.get("required_keys", [])
            if not required_keys or len(required_keys) == 0:
                values = config.get("mappings", {})
                keys = list(values.keys()) if values else []

                if len(keys) > 0:
                    required_keys = keys

            values = config.get("mappings", {})
            if "required_keys" in values:
                required_keys = values.get("required_keys", [])

            row_data.update(
                {
                    "log_id": log_row.log_id,
                    "created_at": log_row.created_at,
                    "evaluation_id": log_row.log_id,
                    "source": log_source,
                    "required_keys": required_keys,
                    "values": config.get("mappings", {}),
                    "output": config.get("output", {}),
                    "input_data_types": config.get("input_data_types", {}),
                }
            )
            if error_localizer:
                row_data.update({"error_details": error_localizer})
            if error_localizer_status:
                row_data["error_localizer_status"] = error_localizer_status
            if error_localizer_message:
                row_data["error_localizer_message"] = error_localizer_message
            if log_source is not None:
                match log_source.lower():
                    case "dataset" | "dataset evaluation":
                        row_data.update({"dataset_id": config.get("dataset_id", None)})
                    case "tracer":
                        row_data.update(
                            {
                                "span_id": config.get("span_id", None),
                                "trace_id": config.get("trace_id", None),
                            }
                        )
                    case "prompt":
                        row_data.update(
                            {
                                "prompt_id": config.get("prompt_id", None),
                            }
                        )
                    case "optimization":
                        row_data.update(
                            {
                                "optimization_id": config.get("optimization_id", None),
                            }
                        )
                    case "experiment":
                        row_data.update(
                            {
                                "experiment_id": config.get("experiment_id", None),
                                "dataset_id": config.get("dataset_id", None),
                            }
                        )
            return self._gm.success_response(row_data)
        except Exception:
            logger.exception("Error fetching log row")
            return self._gm.bad_request(get_error_message("LOG_ROW_FETCHING_FAILED"))

    def patch(self, request, *args, **kwargs):
        try:
            serializer = UpdateColumnConfigSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            validated_data = serializer.validated_data
            eval_id = validated_data.get("eval_id")
            if not eval_id:
                return self._gm.bad_request(get_error_message("EVAL_ID_REQUIRED."))
            column_config = validated_data.get("column_config")

            try:
                setting = EvalSettings.objects.get(
                    eval_id=eval_id,
                    source=validated_data.get("source"),
                    user=request.user,
                )
                setting.column_config = column_config
                setting.save(update_fields=["column_config"])
            except EvalSettings.DoesNotExist:
                EvalSettings.objects.create(
                    eval_id=eval_id,
                    column_config=column_config,
                    source=validated_data.get("source"),
                    user=request.user,
                )
            return self._gm.success_response(
                "Successfully updated column configuration."
            )
        except Exception as e:
            logger.exception(f"Error updating column config: {str(e)}")
            return self._gm.bad_request(get_error_message("COLUMN_CONFIG_NOT_UPDATED"))

    def delete(self, request, *args, **kwargs):
        try:
            log_ids = request.data.get("log_ids", [])
            if not log_ids:
                return self._gm.bad_request(get_error_message("LOG_ID_REQUIRED"))

            logs = APICallLog.objects.filter(log_id__in=log_ids, deleted=False)
            if not logs.exists():
                return self._gm.bad_request(get_error_message("LOGS_NOT_FOUND"))

            logs.update(deleted=True, deleted_at=timezone.now())

            return self._gm.success_response(
                "Successfully deleted the selected log entries."
            )

        except Exception as e:
            logger.exception(f"Error in deleting logs: {str(e)}")
            return self._gm.bad_request(get_error_message("ERROR_DELETING_LOG"))


class CellErrorLocalizerView(APIView):
    """
    On-demand error localization for a single dataset cell.

    Use case: in the dataset detail drawer, the user opens an eval cell
    that doesn't have an `error_analysis` block (because the eval was
    run before error_localization was enabled, or the user wants a
    fresh run). They click "Run error localization" and we:

      1. Look up the cell + its UserEvalMetric (column.source_id) +
         the EvalTemplate.
      2. Resolve the metric's `mapping` (template_var → column UUID)
         against the row's other cells to build `input_data`.
      3. Pull the eval verdict + reason from `cell.value` /
         `cell.value_infos`.
      4. Upsert an `ErrorLocalizerTask(source=DATASET, source_id=cell.id,
         status=PENDING)` so the existing 30s Temporal schedule picks it
         up and processes it via `process_single_error_localization`.

    Returns the task id + status. The frontend then polls the cell
    detail / task status endpoint until `error_analysis` lands in
    `cell.value_infos`.

    POST /model-hub/cells/{cell_id}/run-error-localizer/
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, cell_id=None, *args, **kwargs):
        try:
            from model_hub.models.develop_dataset import Cell
            from model_hub.models.error_localizer_model import (
                ErrorLocalizerSource,
                ErrorLocalizerStatus,
                ErrorLocalizerTask,
            )
            from model_hub.models.evals_metric import UserEvalMetric
            from model_hub.tasks.user_evaluation import (
                _get_input_type,
                _validate_error_localizer_fields,
            )

            org = getattr(request, "organization", None) or request.user.organization

            try:
                cell = Cell.objects.select_related("column", "row", "dataset").get(
                    id=cell_id, deleted=False
                )
            except Cell.DoesNotExist:
                return self._gm.not_found("Cell not found.")

            if cell.dataset and cell.dataset.organization_id != org.id:
                return self._gm.not_found("Cell not found.")

            column = cell.column
            if column.source not in ("evaluation", "experiment_evaluation"):
                return self._gm.bad_request(
                    "Error localization is only available for evaluation cells."
                )

            try:
                uem = UserEvalMetric.objects.select_related("template").get(
                    id=column.source_id
                )
            except UserEvalMetric.DoesNotExist:
                return self._gm.bad_request(
                    "Could not find the evaluation metric for this cell."
                )

            template = uem.template
            if not template:
                return self._gm.bad_request(
                    "The underlying eval template no longer exists."
                )

            metric_config = uem.config or {}
            mapping = metric_config.get("mapping") or {}

            # Build input_data: resolve each template variable to its column
            # value on the same row.
            input_data = {}
            row_id = cell.row_id
            if mapping:
                col_ids = [
                    str(v)
                    for v in mapping.values()
                    if isinstance(v, str) and len(v) == 36
                ]
                # Bulk fetch the source cells in one query
                source_cells = {
                    str(c.column_id): c
                    for c in Cell.objects.filter(
                        row_id=row_id, column_id__in=col_ids, deleted=False
                    )
                }
                for var_name, col_uuid in mapping.items():
                    if not isinstance(col_uuid, str):
                        continue
                    src = source_cells.get(str(col_uuid))
                    if src is not None:
                        input_data[var_name] = src.value or ""

            # If the mapping was empty (no template vars), there's nothing
            # for the localizer to chew on.
            if not input_data:
                return self._gm.bad_request(
                    "Cannot run error localization — this eval has no input "
                    "variable mapping. Add at least one mapping in the eval "
                    "config and re-run the eval first."
                )

            # Pull the eval verdict + explanation from the cell.
            value_infos = cell.value_infos
            if isinstance(value_infos, str):
                try:
                    value_infos = json.loads(value_infos)
                except Exception:
                    value_infos = {}
            if not isinstance(value_infos, dict):
                value_infos = {}

            eval_result = cell.value or ""
            eval_explanation = value_infos.get("reason") or ""

            input_keys = list(input_data.keys())
            input_types = _get_input_type(input_data)
            rule_prompt = (
                (template.config or {}).get("rule_prompt")
                or template.criteria
                or template.description
            )

            initial_status, error_message = _validate_error_localizer_fields(
                rule_prompt, input_data, eval_result
            )

            workspace = cell.dataset.workspace if cell.dataset else None
            if not workspace:
                from accounts.models.workspace import Workspace

                workspace = Workspace.objects.filter(
                    organization=org, is_default=True, is_active=True
                ).first()

            # Upsert the task. If a previous task already exists for this
            # cell (e.g. failed run), reset it to PENDING and let the
            # schedule pick it up again.
            task = ErrorLocalizerTask.objects.filter(source_id=cell.id).first()
            if task:
                task.eval_template = template
                task.eval_result = eval_result
                task.eval_explanation = eval_explanation
                task.input_data = input_data
                task.input_keys = input_keys
                task.input_types = input_types
                task.rule_prompt = rule_prompt
                task.status = initial_status
                task.error_message = error_message
                task.error_analysis = {}
                task.selected_input_key = None
                task.save()
            else:
                task = ErrorLocalizerTask.objects.create(
                    eval_template=template,
                    source=ErrorLocalizerSource.DATASET,
                    source_id=cell.id,
                    input_data=input_data,
                    input_keys=input_keys,
                    input_types=input_types,
                    eval_result=eval_result,
                    eval_explanation=eval_explanation,
                    rule_prompt=rule_prompt,
                    organization=org,
                    workspace=workspace,
                    status=initial_status,
                    error_message=error_message,
                )

            return self._gm.success_response(
                {
                    "task_id": str(task.id),
                    "cell_id": str(cell.id),
                    "status": task.status,
                    "error_message": task.error_message,
                }
            )
        except Exception as e:
            logger.exception(f"Error in CellErrorLocalizerView: {str(e)}")
            return self._gm.bad_request(f"Failed to start error localization: {str(e)}")

    def get(self, request, cell_id=None, *args, **kwargs):
        """
        Poll endpoint — returns the current state of the localizer task
        for a given cell, including the analysis once completed.
        """
        try:
            from model_hub.models.develop_dataset import Cell
            from model_hub.models.error_localizer_model import ErrorLocalizerTask

            org = getattr(request, "organization", None) or request.user.organization
            try:
                cell = Cell.objects.select_related("dataset").get(
                    id=cell_id, deleted=False
                )
            except Cell.DoesNotExist:
                return self._gm.not_found("Cell not found.")
            if cell.dataset and cell.dataset.organization_id != org.id:
                return self._gm.not_found("Cell not found.")

            # Prefer task row when present, but fall back to stored cell metadata
            # so callers can still retrieve results after task lifecycle changes.
            stored_error_analysis = None
            stored_selected_input_key = None
            stored_input_data = None
            stored_input_types = None
            value_infos = cell.value_infos
            if isinstance(value_infos, str):
                try:
                    value_infos = json.loads(value_infos)
                except Exception:
                    value_infos = {}
            if isinstance(value_infos, dict):
                stored_error_analysis = value_infos.get("error_analysis")
                stored_selected_input_key = value_infos.get("selected_input_key")
                stored_input_data = value_infos.get("input_data")
                stored_input_types = value_infos.get("input_types")

            task = ErrorLocalizerTask.objects.filter(source_id=cell.id).first()
            if not task:
                # If analysis already landed on the cell, surface it as a completed state.
                if stored_error_analysis is not None:
                    return self._gm.success_response(
                        {
                            "cell_id": str(cell.id),
                            "status": "completed",
                            "error_analysis": stored_error_analysis,
                            "selected_input_key": stored_selected_input_key,
                            "input_data": stored_input_data,
                            "input_types": stored_input_types,
                            "error_message": None,
                        }
                    )
                return self._gm.success_response(
                    {
                        "cell_id": str(cell.id),
                        "status": None,
                        "error_analysis": None,
                        "selected_input_key": None,
                        "input_data": None,
                        "input_types": None,
                        "error_message": None,
                    }
                )
            return self._gm.success_response(
                {
                    "task_id": str(task.id),
                    "cell_id": str(cell.id),
                    "status": task.status,
                    "error_analysis": (
                        task.error_analysis
                        if task.error_analysis is not None
                        else stored_error_analysis
                    ),
                    "selected_input_key": (
                        task.selected_input_key
                        if task.selected_input_key is not None
                        else stored_selected_input_key
                    ),
                    "input_data": (
                        task.input_data
                        if task.input_data is not None
                        else stored_input_data
                    ),
                    "input_types": (
                        task.input_types
                        if task.input_types is not None
                        else stored_input_types
                    ),
                    "error_message": task.error_message,
                }
            )
        except Exception as e:
            logger.exception(f"Error in CellErrorLocalizerView GET: {str(e)}")
            return self._gm.bad_request(
                f"Failed to fetch error localization status: {str(e)}"
            )


class EvalMetricView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            eval_template_id = request.query_params.get("eval_template_id", None)
            filters_param = request.query_params.get("filters", "[]")

            try:
                filters = json.loads(filters_param) if filters_param else []
            except json.JSONDecodeError:
                filters = []

            if not eval_template_id:
                return self._gm.bad_request({"error": "No eval template id provided"})

            logs = APICallLog.objects.filter(
                source_id=eval_template_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                status=APICallStatusChoices.SUCCESS.value,
            )
            eval_template = EvalTemplate.no_workspace_objects.filter(
                id=eval_template_id
            ).first()
            response_data = get_eval_metric_data(eval_template, filters, logs)

            return self._gm.success_response(response_data)
        except Exception as e:
            logger.exception(f"Error in EvalMetricView.get: {str(e)}")
            return self._gm.bad_request(str(e))

    def post(self, request, *args, **kwargs):
        try:
            eval_template_id = request.data.get("eval_template_id", None)
            filters = request.data.get("filters", [])

            if not eval_template_id:
                return self._gm.bad_request({"error": "No eval template id provided"})

            logs = APICallLog.objects.filter(
                source_id=eval_template_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                status=APICallStatusChoices.SUCCESS.value,
            )
            eval_template = EvalTemplate.no_workspace_objects.filter(
                id=eval_template_id
            ).first()
            response_data = get_eval_metric_data(eval_template, filters, logs)

            return self._gm.success_response(response_data)
        except Exception as e:
            logger.exception(f"Error in EvalMetricView.post: {str(e)}")
            return self._gm.bad_request(str(e))


class GetEvalTemplateNameView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            logs = APICallLog.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization
            )
            log_ids = [
                log.source_id
                for log in logs
                if log.source_id is not None and log.source_id != ""
            ]
            eval_ids = EvalTemplate.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                owner=OwnerChoices.USER.value,
            )
            eval_ids = [eval.id for eval in eval_ids]
            log_ids += eval_ids

            search_text = request.data.get("search_text", "")
            eval_templates = EvalTemplate.no_workspace_objects.filter(id__in=log_ids)
            if search_text:
                eval_templates = eval_templates.filter(name__icontains=search_text)
            eval_template_names = [
                {
                    "id": str(eval_template.id),
                    "name": eval_template.name,
                    "description": eval_template.description,
                }
                for eval_template in eval_templates
            ]
            return self._gm.success_response(eval_template_names)
        except Exception as e:
            logger.exception(f"Error getting eval template names: {str(e)}")
            return self._gm.bad_request(str(e))


class GetEvalTemplates(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def process_graph_data_and_send_ws(
        self, dates, template_logs_map, data, template_map, start_date
    ):
        try:
            # Use pre-fetched logs instead of querying database again
            eval_template_id = data.get("id")
            eval_template = template_map.get(str(eval_template_id), None)

            template_logs = template_logs_map.get(str(eval_template_id), [])

            daily_averages = []
            error_rates = []

            for day in dates:
                # Filter logs for this day in memory
                day_logs = [
                    log for log in template_logs if log["created_at"].date() == day
                ]

                average = calculate_eval_average(eval_template, day_logs)
                daily_averages.append({"date": day, "value": average})

                # Count error logs in memory
                error_rate = len(
                    [
                        log
                        for log in day_logs
                        if log["status"] == APICallStatusChoices.ERROR.value
                    ]
                )
                error_rates.append({"date": day, "value": error_rate})

            avg_graph_data = self.generate_date_range_data(start_date, daily_averages)
            error_rate_data = self.generate_date_range_data(start_date, error_rates)

            if daily_averages:
                max_avg = max(daily_averages, key=lambda x: x["value"])
            else:
                max_avg = None

            if error_rates:
                max_error_rate = max(error_rates, key=lambda x: x["value"])
            else:
                max_error_rate = None

            max_avg_value = max_avg["value"] if max_avg else 0
            max_error_rate_value = max_error_rate["value"] if max_error_rate else 0
            max_axis = math.ceil(max(max_avg_value, max_error_rate_value))

            new_average = {
                "avg_graph_data": avg_graph_data,
            }

            data.update(
                {
                    "average": new_average,
                    "error_rate": error_rate_data,
                    "max_axis": max_axis,
                }
            )
            return data

        except Exception as e:
            logger.exception(
                f"Error pushing graph data for template {str(eval_template_id)}: {e}"
            )

    def generate_date_range_data(self, start_date, template_data):
        """Generate time series data for the last 30 days"""
        date_range = []
        current_date = start_date

        # Create a lookup dict for existing data
        data_lookup = (
            {item["date"]: item["value"] for item in template_data}
            if template_data
            else {}
        )

        for _ in range(31):
            date_str = current_date.strftime("%Y-%m-%dT00:00:00")
            date_range.append(
                {
                    "timestamp": date_str,
                    "value": data_lookup.get(current_date.date(), 0),
                }
            )
            current_date += timedelta(days=1)

        return date_range

    def prepare_template_data(self, template, template_logs_map):
        template_id = str(template.id)
        call_logs = template_logs_map.get(template_id, [])

        # Calculate metrics efficiently
        last30_run = len(call_logs)

        # Get updated_at efficiently
        if call_logs and len(call_logs) > 0:
            # Sort in memory instead of database query
            latest_log = max(call_logs, key=lambda x: x["updated_at"])
            updated_at = latest_log["updated_at"].isoformat()
        else:
            updated_at = template.updated_at.isoformat()

        # Calculate average efficiently
        average = (
            calculate_eval_average(template, call_logs)
            if call_logs and len(call_logs) > 0
            else 0
        )

        template_data = {
            "id": template_id,
            "max_axis": None,
            "eval_template_name": template.name,
            "average": {
                "average": average,
                "avg_graph_data": [],
            },
            "error_rate": [],
            "last30_run": last30_run,
            "updated_at": updated_at,
        }

        return template_data

    def post(self, request, *args, **kwargs):
        try:
            page_size = request.data.get("page_size", 10)
            current_page = request.data.get("current_page_index", 0)
            search_text = request.data.get("search_text", "")
            sort_config_list = request.data.get("sort", [])
            sort_config = sort_config_list[0] if len(sort_config_list) > 0 else {}
            # Calculate date range
            end_date = timezone.now()
            start_date = end_date - timedelta(days=30)

            used_template_ids = list(
                APICallLog.objects.filter(
                    organization=getattr(request, "organization", None)
                    or request.user.organization
                )
                .exclude(source_id__isnull=True)
                .exclude(source_id__exact="")
                .values_list("source_id", flat=True)
                .distinct()
            )

            if not used_template_ids:
                return self._gm.success_response(
                    {
                        "row_data": [],
                        "total_rows": 0,
                        "data_available": False,
                    }
                )

            sort_by = sort_config.get("column_id", "updated_at")
            sort_order = sort_config.get("type", "descending")

            if sort_order == "descending":
                sort_order = "DESC"
            else:
                sort_order = "ASC"

            # Paginate FIRST to get only the template IDs we need
            rows = SQLQueryHandler.get_all_templates(
                used_template_ids,
                search_text,
                (
                    getattr(request, "organization", None) or request.user.organization
                ).id,
                sort_order,
                sort_by,
                page_size,
                current_page * page_size,
                (
                    getattr(request, "workspace", None).id
                    if getattr(request, "workspace", None)
                    else None
                ),
            )
            paginated_data = []
            paginated_template_ids = []
            total_rows = 0
            for row in rows:
                result = {
                    "id": row[0],
                    "max_axis": None,
                    "eval_template_name": row[1],
                    "average": {"avg_graph_data": [], "average": 0},
                    "error_rate": [],
                    "last30_run": row[2],
                    "updated_at": row[3],
                }

                paginated_data.append(result)
                paginated_template_ids.append(row[0])
                total_rows = row[4]

            # Fetch logs ONLY for paginated templates (not all templates)
            logs = APICallLog.objects.filter(
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
                created_at__gte=start_date,
                source_id__in=paginated_template_ids,
            ).values("source_id", "created_at", "status", "config", "updated_at")

            template_logs_map = defaultdict(list)
            for log in logs:
                if log["source_id"] is not None:
                    template_logs_map[str(log["source_id"])].append(log)

            templates = list(
                EvalTemplate.no_workspace_objects.filter(id__in=paginated_template_ids)
            )
            template_map = {str(template.id): template for template in templates}
            # Get dates efficiently for paginated templates
            dates = set()
            for template_id in paginated_template_ids:
                template_logs = template_logs_map.get(str(template_id), [])
                for log in template_logs:
                    dates.add(log["created_at"].date())
            dates = sorted(dates)

            final_data = []

            # Helper function to bind context for thread pool execution
            def process_graph_data(data):
                return self.process_graph_data_and_send_ws(
                    dates, template_logs_map, data, template_map, start_date
                )

            # Wrap function with OTel context propagation for thread safety
            wrapped_process_graph_data = wrap_for_thread(process_graph_data)

            with ThreadPoolExecutor(max_workers=20) as executor:
                results = list(
                    executor.map(
                        wrapped_process_graph_data,
                        paginated_data,
                    )
                )
                final_data.extend(results)

            return self._gm.success_response(
                {
                    "row_data": final_data,
                    "total_rows": total_rows,
                    "data_available": len(paginated_template_ids) != 0,
                }
            )

        except Exception as e:
            logger.error(
                f"Error in GetEvalTemplates: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateListView(APIView):
    """
    POST /model-hub/eval-templates/list/

    Returns paginated eval template list with filtering, search, and 30-day metrics.
    All inputs and outputs are validated with Pydantic schemas.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from model_hub.serializers.eval_list import EvalListRequestSerializer
        from model_hub.types import EvalListItem, EvalListResponse
        from model_hub.utils.eval_list import (
            build_eval_list_queryset,
            compute_thirty_day_data,
            derive_eval_type,
            derive_output_type,
            get_created_by_name,
        )

        try:
            # 1. Validate request via DRF serializer (errors auto-handled)
            serializer = EvalListRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            req = serializer.validated_data

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            workspace = getattr(request, "workspace", None)

            # 2. Build queryset with prefetch to avoid N+1
            qs = build_eval_list_queryset(
                organization=organization,
                workspace=workspace,
                owner_filter=req.get("owner_filter", "all"),
                search=req.get("search"),
                filters=req.get("filters"),
            )

            # Prefetch evaluators + user and versions + user to avoid N+1 in get_created_by_name
            from django.db.models import Prefetch

            from model_hub.models.evals_metric import EvalTemplateVersion, Evaluator

            qs = qs.prefetch_related(
                Prefetch(
                    "evaluators",
                    queryset=Evaluator.objects.select_related("user").filter(
                        user__isnull=False
                    )[:1],
                    to_attr="_prefetched_evaluators",
                ),
            )

            # 3. Sort
            order_field = req.get("sort_by", "updated_at")
            if req.get("sort_order", "desc") == "desc":
                order_field = f"-{order_field}"
            qs = qs.order_by(order_field)

            # 4. Handle eval_type filter
            filters = req.get("filters") or {}
            eval_type_filter = (
                filters.get("eval_type")
                if isinstance(filters, dict)
                else getattr(filters, "eval_type", None)
            )
            if eval_type_filter:
                # Keep list filtering aligned with UI display for composites:
                # composite parent eval_type may be stale/default, but displayed
                # type is derived from child templates.
                non_composite_q = Q(eval_type__in=eval_type_filter) & ~Q(
                    template_type="composite"
                )
                composite_q = Q(
                    template_type="composite",
                    composite_children__deleted=False,
                    composite_children__child__eval_type__in=eval_type_filter,
                )
                qs = qs.filter(non_composite_q | composite_q).distinct()

            eval_type_not_filter = (
                filters.get("eval_type_not")
                if isinstance(filters, dict)
                else getattr(filters, "eval_type_not", None)
            )
            if eval_type_not_filter:
                exc_non_composite = Q(eval_type__in=eval_type_not_filter) & ~Q(
                    template_type="composite"
                )
                exc_composite = Q(
                    template_type="composite",
                    composite_children__deleted=False,
                    composite_children__child__eval_type__in=eval_type_not_filter,
                )
                qs = qs.exclude(exc_non_composite | exc_composite).distinct()

            total = qs.count()
            page = req.get("page", 0)
            page_size = req.get("page_size", 25)
            offset = page * page_size
            templates = list(qs[offset : offset + page_size])

            # 6. Bulk-fetch version creator names for user-owned templates
            user_template_ids = [
                str(t.id) for t in templates if t.owner != OwnerChoices.SYSTEM.value
            ]
            version_creators = {}
            if user_template_ids:
                versions = (
                    EvalTemplateVersion.objects.filter(
                        eval_template_id__in=user_template_ids, created_by__isnull=False
                    )
                    .select_related("created_by")
                    .order_by("eval_template_id", "version_number")
                    .distinct("eval_template_id")
                )
                for v in versions:
                    name = getattr(v.created_by, "name", "") or ""
                    version_creators[str(v.eval_template_id)] = (
                        name.strip() if name.strip() else v.created_by.email
                    )

            # 8. Build response items
            items = []
            for template in templates:
                tid = str(template.id)

                eval_type = derive_eval_type(template)

                # Fast created_by resolution
                if template.owner == OwnerChoices.SYSTEM.value:
                    created_by = "System"
                else:
                    # Try prefetched evaluators first
                    prefetched = getattr(template, "_prefetched_evaluators", [])
                    if prefetched and prefetched[0].user:
                        u = prefetched[0].user
                        created_by = (getattr(u, "name", "") or "").strip() or u.email
                    else:
                        created_by = version_creators.get(tid, "User")

                items.append(
                    EvalListItem(
                        id=tid,
                        name=template.name,
                        template_type=template.template_type or "single",
                        eval_type=eval_type,
                        output_type=derive_output_type(template),
                        owner=(
                            "system"
                            if template.owner == OwnerChoices.SYSTEM.value
                            else "user"
                        ),
                        created_by_name=created_by,
                        version_count=1,
                        current_version="V1",
                        last_updated=template.updated_at.isoformat(),
                        thirty_day_chart=[],
                        thirty_day_error_rate=[],
                        thirty_day_run_count=0,
                        tags=template.eval_tags or [],
                    )
                )

            # 8. Return validated response
            response = EvalListResponse(
                items=[item.model_dump() for item in items],
                total=total,
                page=page,
                page_size=page_size,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateListView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateListChartsView(APIView):
    """
    POST /model-hub/eval-templates/list-charts/

    Returns 30-day chart data (run counts + error rates) for a list of template IDs.
    Uses ClickHouse for fast analytics. Called separately from the list API so the
    table renders instantly while charts load async.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            template_ids = request.data.get("template_ids", [])
            if not template_ids:
                return self._gm.success_response({"charts": {}})

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            charts = self._fetch_charts_from_postgres(organization, template_ids)

            return self._gm.success_response({"charts": charts})

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateListChartsView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))

    def _fetch_charts_from_postgres(self, organization, template_ids):
        """
        Query PostgreSQL for 30-day daily run counts and failure rates per template.
        Failure = API error OR eval result is "Failed"/"Fail"/score 0.
        Uses the same data source as the detail page so results are always fresh.
        Returns: { template_id: { chart: [...], errorRate: [...], runCount: N } }
        """
        import json as _json
        from collections import defaultdict
        from datetime import date, timedelta

        from django.utils import timezone

        start_date = timezone.now() - timedelta(days=30)

        # Fetch individual logs to inspect config.output for pass/fail
        if APICallLog is None:
            return []
        logs = (
            APICallLog.objects.filter(
                organization=organization,
                source_id__in=[str(tid) for tid in template_ids],
                created_at__gte=start_date,
                deleted=False,
            )
            .values("source_id", "created_at", "status", "config")
            .order_by("source_id", "created_at")
        )

        # Build per-template daily data
        daily_data = defaultdict(
            lambda: defaultdict(lambda: {"total": 0, "failures": 0})
        )
        for log in logs:
            day = log["created_at"].date()
            sid = log["source_id"]
            daily_data[sid][day]["total"] += 1

            # Count as failure if API error or eval result is Failed/Fail/0
            if log["status"] == APICallStatusChoices.ERROR.value:
                daily_data[sid][day]["failures"] += 1
            else:
                config = log.get("config") or {}
                if isinstance(config, str):
                    try:
                        config = _json.loads(config)
                    except (ValueError, TypeError):
                        config = {}
                output = config.get("output", {})
                if isinstance(output, dict):
                    result = output.get("output")
                    if result in ("Failed", "Fail"):
                        daily_data[sid][day]["failures"] += 1
                    elif result == 0 or result == 0.0:
                        daily_data[sid][day]["failures"] += 1

        # Generate 31-day time series for each template
        today = date.today()
        start = today - timedelta(days=30)
        result = {}

        for tid in template_ids:
            chart = []
            error_rate = []
            run_count = 0
            tid_str = str(tid)

            for i in range(31):
                day = start + timedelta(days=i)
                ts = day.strftime("%Y-%m-%dT00:00:00")
                day_data = daily_data.get(tid_str, {}).get(
                    day, {"total": 0, "failures": 0}
                )
                total = day_data["total"]
                failures = day_data["failures"]
                chart.append({"timestamp": ts, "value": total})
                rate = round((failures / total) * 100, 1) if total > 0 else 0
                error_rate.append({"timestamp": ts, "value": rate})
                run_count += total

            result[tid_str] = {
                "chart": chart,
                "error_rate": error_rate,
                "run_count": run_count,
            }

        return result


class EvalTemplateBulkDeleteView(APIView):
    """
    POST /model-hub/eval-templates/bulk-delete/

    Soft-delete multiple eval templates. Only user-owned templates can be deleted.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from model_hub.types import BulkDeleteRequest, BulkDeleteResponse

        try:
            try:
                req = BulkDeleteRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            deleted_count = EvalTemplate.objects.filter(
                id__in=req.template_ids,
                organization=organization,
                owner=OwnerChoices.USER.value,
                deleted=False,
            ).update(deleted=True)

            response = BulkDeleteResponse(deleted_count=deleted_count)
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateBulkDeleteView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateCreateV2View(APIView):
    """
    POST /model-hub/eval-templates/create-v2/

    Create a single eval template with the revamped schema.
    Supports the new scoring fields (pass_threshold, choice_scores, output_type_normalized).
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import re

        from model_hub.types import EvalCreateRequest, EvalCreateResponse
        from model_hub.utils.scoring import (
            validate_choice_scores,
            validate_pass_threshold,
        )

        try:
            # 1. Validate request
            try:
                req = EvalCreateRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            workspace = getattr(request, "workspace", None)

            # For drafts: generate a temp name, skip validations
            is_draft = req.is_draft
            if is_draft:
                import uuid as _uuid

                cleaned_name = f"draft-{_uuid.uuid4().hex[:8]}"
            else:
                # 2. Validate name format
                cleaned_name = req.name.strip()
                if not cleaned_name:
                    return self._gm.bad_request("Name is required.")
                if not re.match(r"^[a-z0-9_-]+$", cleaned_name):
                    return self._gm.bad_request(
                        "Name can only contain lowercase letters, numbers, hyphens (-), or underscores (_)."
                    )
                if cleaned_name.startswith(("-", "_")) or cleaned_name.endswith(
                    ("-", "_")
                ):
                    return self._gm.bad_request(
                        "Name cannot start or end with hyphens (-) or underscores (_)."
                    )
                if "_-" in cleaned_name or "-_" in cleaned_name:
                    return self._gm.bad_request(
                        "Name cannot contain consecutive separators (_- or -_)."
                    )

                # 3. Check name uniqueness
                if (
                    EvalTemplate.objects.filter(
                        name=cleaned_name,
                        organization=organization,
                        deleted=False,
                    ).exists()
                    or EvalTemplate.no_workspace_objects.filter(
                        name=cleaned_name,
                        owner=OwnerChoices.SYSTEM.value,
                        deleted=False,
                    ).exists()
                ):
                    return self._gm.bad_request(
                        "An evaluation with this name already exists."
                    )

            # 4. Validate instructions/code (skip for drafts)
            if not is_draft:
                if req.eval_type == "code":
                    if not req.code:
                        return self._gm.bad_request(
                            "Code is required for code-type evaluations."
                        )
                else:
                    variable_pattern = r"\{\{\s*[^{}]+?\s*\}\}"
                    has_data_injection = (
                        (
                            req.data_injection
                            and (
                                req.data_injection.get("full_row")
                                or req.data_injection.get("fullRow")
                                or not req.data_injection.get("variables_only", True)
                                or not req.data_injection.get("variablesOnly", True)
                            )
                        )
                        if hasattr(req, "data_injection") and req.data_injection
                        else False
                    )
                    if (
                        req.instructions
                        and not re.search(variable_pattern, req.instructions)
                        and not has_data_injection
                    ):
                        return self._gm.bad_request(
                            "Instructions must contain at least one template variable "
                            "using double curly braces (e.g. {{variable_name}}), or "
                            "enable data injection to evaluate without mapping."
                        )
                    if not req.instructions:
                        # Diagnostic: log the raw payload keys so any future
                        # mis-cased caller (e.g. `isDraft` without `is_draft`)
                        # is visible. We already alias `isDraft` → `is_draft`
                        # on the model, so this branch only fires when the
                        # caller genuinely omitted both draft intent and
                        # instructions. See TH-4076.
                        logger.warning(
                            "create-v2 rejecting empty instructions; payload_keys=%s",
                            sorted((request.data or {}).keys()),
                        )
                        return self._gm.bad_request("Instructions are required.")

            # 5. Validate scoring fields
            if req.output_type == "deterministic":
                if not req.choice_scores:
                    return self._gm.bad_request(
                        "choice_scores is required when output_type is 'deterministic'."
                    )
                errors = validate_choice_scores(req.choice_scores)
                if errors:
                    return self._gm.bad_request("; ".join(errors))

            threshold_errors = validate_pass_threshold(req.pass_threshold)
            if threshold_errors:
                return self._gm.bad_request("; ".join(threshold_errors))

            # 6. Build config (backward-compatible format)
            # Must match what prepare_user_eval_config produces so the
            # existing eval runner can execute this template.
            output_map = {
                "pass_fail": "Pass/Fail",
                "percentage": "score",
                "deterministic": "choices",
            }
            output_value = output_map.get(req.output_type, "Pass/Fail")

            # Single source of truth for template format
            template_format = getattr(req, "template_format", "mustache")

            # Extract required_keys from instructions (shared).
            # Auto-context roots (row / span / trace / session) and their
            # dotted descendants are NOT user-mappable variables — they are
            # resolved at runtime from the current row / span / trace /
            # session. Strip them from required_keys and auto-enable the
            # matching data_injection flags so the template saves without
            # needing a manual mapping.
            _AUTO_CTX_ROOTS = {"row", "span", "trace", "session", "call"}
            _AUTO_CTX_ROOT_TO_FLAG = {
                "row": "full_row",
                "span": "span_context",
                "trace": "trace_context",
                "session": "session_context",
                "call": "call_context",
            }
            # Collect text from instructions + all messages for variable extraction
            _all_text = [req.instructions or ""]
            if req.messages:
                for msg in req.messages:
                    _all_text.append(msg.get("content", ""))
            _combined_text = "\n".join(t for t in _all_text if t)

            if template_format == "jinja":
                from model_hub.utils.jinja_variables import extract_jinja_variables

                variables = []
                for t in _all_text:
                    if t.strip():
                        variables.extend(extract_jinja_variables(t))
                variables = list(set(variables))
            else:
                variables = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", _combined_text)
                variables = [v.strip() for v in variables]
            _auto_flags_from_instructions: dict = {}
            _filtered_vars = []
            for v in variables:
                head = v.split(".", 1)[0].strip()
                if head in _AUTO_CTX_ROOTS:
                    _auto_flags_from_instructions[_AUTO_CTX_ROOT_TO_FLAG[head]] = True
                else:
                    _filtered_vars.append(v)
            required_keys = list(set(_filtered_vars))

            # Build choices (shared)
            if req.choice_scores:
                choices_list = list(req.choice_scores.keys())
                choices_map = {
                    k: "pass" if v >= 0.7 else ("neutral" if v >= 0.3 else "fail")
                    for k, v in req.choice_scores.items()
                }
            elif req.output_type == "pass_fail":
                choices_list = ["Passed", "Failed"]
                choices_map = {}
            else:
                choices_list = []
                choices_map = {}

            if req.eval_type == "code":
                config = {
                    "output": output_value,
                    "eval_type_id": "CustomCodeEval",
                    "code": req.code,
                    "language": req.code_language or "python",
                    "required_keys": [],
                    "custom_eval": True,
                }
                criteria = req.code or ""
                choices_list = (
                    ["Passed", "Failed"] if req.output_type == "pass_fail" else []
                )

            elif req.eval_type == "agent":
                # Merge auto-detected context flags with any explicit
                # data_injection the caller set. Auto-detected flags win
                # (they reflect what the prompt actually references).
                _merged_data_injection = dict(
                    req.data_injection or {"variables_only": True}
                )
                if _auto_flags_from_instructions:
                    _merged_data_injection.update(_auto_flags_from_instructions)
                    # If any auto-context root was referenced, the template
                    # is no longer variables-only (it also consumes row /
                    # span / trace / session), so clear the flag.
                    _merged_data_injection.pop("variables_only", None)
                    _merged_data_injection.pop("variablesOnly", None)

                config = {
                    "output": output_value,
                    "eval_type_id": "AgentEvaluator",
                    "required_keys": required_keys,
                    "rule_prompt": req.instructions,
                    "custom_eval": True,
                    "check_internet": req.check_internet,
                    "agent_mode": req.mode or "agent",
                    "model": req.model,
                    "tools": req.tools or {},
                    "knowledge_bases": req.knowledge_bases or [],
                    "data_injection": _merged_data_injection,
                    "summary": req.summary or {"type": "concise"},
                    "instructions": req.instructions,
                }
                if choices_map:
                    config["choices"] = choices_list
                    config["choices_map"] = choices_map
                    config["multi_choice"] = False
                criteria = req.instructions

            else:
                # LLM-as-a-judge (default)
                # Build system_prompt from messages if provided
                system_prompt = None
                if req.messages:
                    sys_msgs = [m for m in req.messages if m.get("role") == "system"]
                    if sys_msgs:
                        system_prompt = sys_msgs[0].get("content", "")

                config = {
                    "output": output_value,
                    "eval_type_id": "CustomPromptEvaluator",
                    "required_keys": required_keys,
                    "rule_prompt": req.instructions,
                    "system_prompt": system_prompt,
                    "custom_eval": True,
                    "check_internet": req.check_internet,
                }
                # Store full message chain if provided
                if req.messages and len(req.messages) > 1:
                    config["messages"] = req.messages
                # Store few-shot examples
                if req.few_shot_examples:
                    config["few_shot_examples"] = req.few_shot_examples
                if choices_map:
                    config["choices"] = choices_list
                    config["choices_map"] = choices_map
                    config["multi_choice"] = False
                criteria = req.instructions

            # Store template_format in config
            config["template_format"] = template_format

            # Build eval_tags — category tags only (not type)
            eval_tags = list(req.tags) if req.tags else []

            # 7. Create EvalTemplate
            eval_template = EvalTemplate.objects.create(
                name=cleaned_name,
                organization=organization,
                owner=OwnerChoices.USER.value,
                eval_type=req.eval_type,
                eval_tags=eval_tags,
                config=config,
                choices=choices_list,
                description=req.description or "",
                criteria=criteria,
                multi_choice=False,
                proxy_agi=True,
                visible_ui=not is_draft,
                model=req.model,
                # New scoring fields
                output_type_normalized=req.output_type,
                pass_threshold=req.pass_threshold,
                choice_scores=req.choice_scores,
            )

            # 8. Create initial version (V1)
            from model_hub.models.evals_metric import EvalTemplateVersion

            try:
                EvalTemplateVersion.objects.create_version(
                    eval_template=eval_template,
                    prompt_messages=[],
                    config_snapshot=config,
                    criteria=criteria,
                    model=req.model,
                    user=request.user,
                    organization=organization,
                    workspace=workspace,
                )
            except Exception as ver_err:
                logger.warning(f"Failed to create V1 for eval: {ver_err}")

            # 9. Return response
            response = EvalCreateResponse(
                id=str(eval_template.id),
                name=eval_template.name,
                version="V1",
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateCreateV2View: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateDetailView(APIView):
    """
    GET /model-hub/eval-templates/<id>/detail/

    Fetch a single eval template with all revamped fields.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        from model_hub.types import EvalDetailResponse
        from model_hub.utils.eval_list import (
            derive_eval_type,
            derive_output_type,
            get_created_by_name,
        )

        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            # Check access: system evals are visible to all, user evals only to their org
            if (
                template.owner == OwnerChoices.USER.value
                and template.organization_id != organization.id
            ):
                return self._gm.not_found("Eval template not found.")

            # Get actual version info
            from model_hub.models.evals_metric import EvalTemplateVersion

            version_count = EvalTemplateVersion.objects.filter(
                eval_template=template
            ).count()
            default_version = EvalTemplateVersion.objects.get_default(template)
            current_version_num = (
                default_version.version_number
                if default_version
                else (1 if version_count > 0 else 0)
            )

            # Detail should reflect current template state.
            # Version snapshots are immutable and available in /versions.
            config = template.config or (
                default_version.config_snapshot if default_version else {}
            )
            detail_criteria = template.criteria or (
                default_version.criteria if default_version else ""
            )
            detail_model = template.model or (
                default_version.model if default_version else "turing_large"
            )

            # Normalize legacy short model names to full turing_* values
            _legacy_model_map = {
                "small": "turing_small",
                "large": "turing_large",
                "flash": "turing_flash",
            }
            if detail_model in _legacy_model_map:
                detail_model = _legacy_model_map[detail_model]

            response = EvalDetailResponse(
                id=str(template.id),
                name=template.name,
                description=template.description or "",
                template_type=template.template_type or "single",
                eval_type=derive_eval_type(template),
                instructions=detail_criteria,
                model=detail_model,
                output_type=(
                    template.output_type_normalized
                    if template.output_type_normalized
                    else derive_output_type(template)
                ),
                pass_threshold=(
                    template.pass_threshold
                    if template.pass_threshold is not None
                    else 0.5
                ),
                choice_scores=template.choice_scores,
                choices=template.choices,
                multi_choice=bool(
                    getattr(template, "multi_choice", False)
                    or config.get("multi_choice", False)
                ),
                code=(config.get("code") or None)
                if derive_eval_type(template) == "code"
                else None,
                code_language=config.get("language")
                or config.get("code_language")
                or "python",
                required_keys=config.get("required_keys") or [],
                owner=(
                    "system" if template.owner == OwnerChoices.SYSTEM.value else "user"
                ),
                created_by_name=get_created_by_name(template),
                version_count=max(version_count, 1),
                current_version=(
                    f"V{current_version_num}" if current_version_num > 0 else "V1"
                ),
                tags=template.eval_tags or [],
                check_internet=config.get("check_internet", False),
                error_localizer_enabled=template.error_localizer_enabled,
                template_format=(template.config or {}).get(
                    "template_format", "mustache"
                ),
                aggregation_enabled=template.aggregation_enabled,
                aggregation_function=template.aggregation_function,
                composite_child_axis=template.composite_child_axis or "",
                config=config,
                created_at=(
                    template.created_at.isoformat() if template.created_at else ""
                ),
                updated_at=(
                    template.updated_at.isoformat() if template.updated_at else ""
                ),
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateDetailView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateUpdateView(APIView):
    """
    PUT /model-hub/eval-templates/<id>/update/

    Update an eval template. Only user-owned templates can be updated.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def put(self, request, template_id, *args, **kwargs):
        import re

        from model_hub.types import EvalUpdateRequest, EvalUpdateResponse
        from model_hub.utils.scoring import (
            validate_choice_scores,
            validate_pass_threshold,
        )

        try:
            try:
                req = EvalUpdateRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                template = EvalTemplate.objects.get(
                    id=template_id,
                    organization=organization,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found(
                    "Eval template not found or cannot be edited (system templates are read-only)."
                )

            # Update fields if provided
            if req.name is not None:
                cleaned = req.name.strip()
                if not re.match(r"^[a-z0-9_-]+$", cleaned):
                    return self._gm.bad_request(
                        "Name can only contain lowercase letters, numbers, hyphens, or underscores."
                    )
                # Check uniqueness
                if (
                    EvalTemplate.objects.filter(
                        name=cleaned, organization=organization, deleted=False
                    )
                    .exclude(id=template_id)
                    .exists()
                ):
                    return self._gm.bad_request(
                        "An evaluation with this name already exists."
                    )
                template.name = cleaned

            # Single source of truth for template format
            template_format = req.template_format or (template.config or {}).get(
                "template_format", "mustache"
            )

            if req.instructions is not None:
                # For code evals, `criteria` stores the Python/JS code — don't
                # overwrite it with LLM prompt instructions.
                if template.config.get("eval_type_id") != "CustomCodeEval":
                    template.criteria = req.instructions
                # Update backward-compat config fields.
                # Use the same regex as CREATE (any {{...}}) and strip
                # auto-context roots (row/span/trace/session) from
                # required_keys, merging the matching data_injection flags
                # into the stored config.
                _AUTO_CTX_ROOTS = {"row", "span", "trace", "session", "call"}
                _AUTO_CTX_ROOT_TO_FLAG = {
                    "row": "full_row",
                    "span": "span_context",
                    "trace": "trace_context",
                    "session": "session_context",
                    "call": "call_context",
                }
                # Collect text from instructions + all messages
                _all_text = [req.instructions or ""]
                _msgs = (
                    req.messages
                    if req.messages
                    else (template.config or {}).get("messages", [])
                )
                if _msgs:
                    for msg in _msgs:
                        _all_text.append(
                            msg.get("content", "") if isinstance(msg, dict) else ""
                        )
                _combined_text = "\n".join(t for t in _all_text if t)

                if template_format == "jinja":
                    from model_hub.utils.jinja_variables import extract_jinja_variables

                    _raw_vars = []
                    for t in _all_text:
                        if t.strip():
                            _raw_vars.extend(extract_jinja_variables(t))
                    _raw_vars = list(set(_raw_vars))
                else:
                    _raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", _combined_text)
                    _raw_vars = [v.strip() for v in _raw_vars]
                _auto_flags: dict = {}
                _filtered: list = []
                for v in _raw_vars:
                    head = v.split(".", 1)[0].strip()
                    if head in _AUTO_CTX_ROOTS:
                        _auto_flags[_AUTO_CTX_ROOT_TO_FLAG[head]] = True
                    else:
                        _filtered.append(v)

                if template.config is None:
                    template.config = {}
                template.config["required_keys"] = list(set(_filtered))
                template.config["rule_prompt"] = req.instructions
                if _auto_flags:
                    di = template.config.get("data_injection") or {}
                    di.update(_auto_flags)
                    # Any auto-context root means the template is no
                    # longer variables-only.
                    di.pop("variables_only", None)
                    di.pop("variablesOnly", None)
                    template.config["data_injection"] = di

            if req.model is not None:
                template.model = req.model

            if req.output_type is not None:
                template.output_type_normalized = req.output_type
                output_map = {
                    "pass_fail": "Pass/Fail",
                    "percentage": "score",
                    "deterministic": "choices",
                }
                if template.config is None:
                    template.config = {}
                template.config["output"] = output_map.get(req.output_type, "Pass/Fail")

            if req.pass_threshold is not None:
                errors = validate_pass_threshold(req.pass_threshold)
                if errors:
                    return self._gm.bad_request("; ".join(errors))
                template.pass_threshold = req.pass_threshold

            if "choice_scores" in (request.data or {}):
                if req.choice_scores:
                    errors = validate_choice_scores(req.choice_scores)
                    if errors:
                        return self._gm.bad_request("; ".join(errors))
                    template.choice_scores = req.choice_scores
                    template.choices = list(req.choice_scores.keys())
                    if template.config is None:
                        template.config = {}
                    template.config["choices"] = list(req.choice_scores.keys())
                    template.config["choices_map"] = {
                        k: "pass" if v >= 0.7 else ("neutral" if v >= 0.3 else "fail")
                        for k, v in req.choice_scores.items()
                    }
                else:
                    # Clear choices
                    template.choice_scores = None
                    template.choices = []
                    if template.config:
                        template.config.pop("choices", None)
                        template.config.pop("choices_map", None)

            if req.multi_choice is not None:
                template.multi_choice = req.multi_choice
                if template.config is None:
                    template.config = {}
                template.config["multi_choice"] = req.multi_choice

            if req.description is not None:
                template.description = req.description

            if req.tags is not None:
                template.eval_tags = req.tags

            if req.check_internet is not None:
                if template.config is None:
                    template.config = {}
                template.config["check_internet"] = req.check_internet

            # Code eval fields
            if req.code is not None:
                if template.config is None:
                    template.config = {}
                template.config["code"] = req.code
                template.config["eval_type_id"] = "CustomCodeEval"
                template.eval_type = "code"
                template.criteria = req.code

            if req.code_language is not None:
                if template.config is None:
                    template.config = {}
                template.config["language"] = req.code_language

            # LLM-as-a-judge fields
            if req.messages is not None:
                if template.config is None:
                    template.config = {}
                template.config["messages"] = req.messages

            if req.few_shot_examples is not None:
                if template.config is None:
                    template.config = {}
                template.config["few_shot_examples"] = req.few_shot_examples

            # Agent eval fields
            if req.mode is not None:
                if template.config is None:
                    template.config = {}
                template.config["agent_mode"] = req.mode

            if req.tools is not None:
                if template.config is None:
                    template.config = {}
                template.config["tools"] = req.tools

            if req.knowledge_bases is not None:
                if template.config is None:
                    template.config = {}
                template.config["knowledge_bases"] = req.knowledge_bases

            if req.data_injection is not None:
                if template.config is None:
                    template.config = {}
                template.config["data_injection"] = req.data_injection

            if req.summary is not None:
                if template.config is None:
                    template.config = {}
                template.config["summary"] = req.summary

            # eval_type change: rewrite eval_type_id so the runtime routes to
            # the correct evaluator class. Applied last so other config edits
            # above land in the same save.
            if req.eval_type is not None:
                _EVAL_TYPE_ID_MAP = {
                    "agent": "AgentEvaluator",
                    "llm": "CustomPromptEvaluator",
                    "code": "CustomCodeEval",
                }
                template.eval_type = req.eval_type
                if template.config is None:
                    template.config = {}
                template.config["eval_type_id"] = _EVAL_TYPE_ID_MAP[req.eval_type]

            # Error Localization (Phase 19)
            if req.error_localizer_enabled is not None:
                template.error_localizer_enabled = req.error_localizer_enabled

            # Store template_format in config
            if template.config is None:
                template.config = {}
            template.config["template_format"] = template_format

            # Publish draft → make visible in UI
            if req.publish:
                template.visible_ui = True

            template.save()

            response = EvalUpdateResponse(
                id=str(template.id),
                name=template.name,
                updated=True,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateUpdateView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateVersionListView(APIView):
    """
    GET /model-hub/eval-templates/<id>/versions/

    List all versions for an eval template.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.types import EvalVersionItem, EvalVersionListResponse

        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Verify template exists and user has access
            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            # Check org access for user evals
            if (
                template.owner == OwnerChoices.USER.value
                and template.organization_id
                and template.organization_id != organization.id
            ):
                return self._gm.not_found("Eval template not found.")

            versions = (
                EvalTemplateVersion.objects.filter(eval_template_id=template_id)
                .select_related("created_by")
                .order_by("-version_number")
            )

            items = []
            for v in versions:
                created_by_name = ""
                if v.created_by:
                    created_by_name = (
                        getattr(v.created_by, "name", "") or v.created_by.email
                    )
                items.append(
                    EvalVersionItem(
                        id=str(v.id),
                        version_number=v.version_number,
                        is_default=v.is_default,
                        criteria=v.criteria or "",
                        model=v.model or "",
                        config_snapshot=v.config_snapshot or {},
                        created_by_name=created_by_name,
                        created_at=v.created_at.isoformat() if v.created_at else "",
                    )
                )

            response = EvalVersionListResponse(
                template_id=str(template_id),
                versions=[item.model_dump() for item in items],
                total=len(items),
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateVersionListView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalTemplateVersionCreateView(APIView):
    """
    POST /model-hub/eval-templates/<id>/versions/create/

    Create a new version snapshot from the current template state.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.types import CreateVersionRequest, CreateVersionResponse

        try:
            try:
                req = CreateVersionRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                template = EvalTemplate.objects.get(
                    id=template_id,
                    organization=organization,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found or not editable.")

            # Create new version from current template state (or overrides)
            version = EvalTemplateVersion.objects.create_version(
                eval_template=template,
                prompt_messages=[],
                config_snapshot=req.config_snapshot or template.config or {},
                criteria=req.criteria or template.criteria or "",
                model=req.model or template.model or "",
                user=request.user,
                organization=organization,
                workspace=getattr(template, "workspace", None),
            )

            # Only set as default if this is the first version (no existing default)
            has_default = (
                EvalTemplateVersion.objects.filter(
                    eval_template=template, is_default=True
                )
                .exclude(id=version.id)
                .exists()
            )
            if not has_default:
                version.is_default = True
                version.save(update_fields=["is_default"])

            response = CreateVersionResponse(
                id=str(version.id),
                version_number=version.version_number,
                is_default=version.is_default,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in EvalTemplateVersionCreateView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class SetDefaultVersionView(APIView):
    """
    PUT /model-hub/eval-templates/<id>/versions/<version_id>/set-default/

    Set a specific version as the default (active) version.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def put(self, request, template_id, version_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalTemplateVersion

        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                template = EvalTemplate.objects.get(
                    id=template_id,
                    organization=organization,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found or not editable.")

            try:
                version = EvalTemplateVersion.objects.get(
                    id=version_id, eval_template=template
                )
            except EvalTemplateVersion.DoesNotExist:
                return self._gm.not_found("Version not found.")

            # Unset all defaults, then set this one
            with transaction.atomic():
                EvalTemplateVersion.objects.filter(
                    eval_template=template, is_default=True
                ).update(is_default=False)
                version.is_default = True
                version.save(update_fields=["is_default"])
                # Align template state with the active default version so
                # runtime and detail page resolve from the same config.
                template.config = version.config_snapshot or {}
                template.criteria = version.criteria or ""
                template.model = version.model or template.model
                template.save(
                    update_fields=["config", "criteria", "model", "updated_at"]
                )

            return self._gm.success_response(
                {
                    "id": str(version.id),
                    "version_number": version.version_number,
                    "is_default": True,
                }
            )

        except Exception as e:
            logger.error(
                f"Error in SetDefaultVersionView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class RestoreVersionView(APIView):
    """
    POST /model-hub/eval-templates/<id>/versions/<version_id>/restore/

    Restore a version by creating a new version with the old version's config.
    Does NOT modify the old version — creates a new one on top.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, template_id, version_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalTemplateVersion

        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                template = EvalTemplate.objects.get(
                    id=template_id,
                    organization=organization,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found or not editable.")

            try:
                source_version = EvalTemplateVersion.objects.get(
                    id=version_id, eval_template=template
                )
            except EvalTemplateVersion.DoesNotExist:
                return self._gm.not_found("Version not found.")

            # Create a new version with the source version's config
            new_version = EvalTemplateVersion.objects.create_version(
                eval_template=template,
                prompt_messages=source_version.prompt_messages or [],
                config_snapshot=source_version.config_snapshot or {},
                criteria=source_version.criteria or "",
                model=source_version.model or "",
                user=request.user,
                organization=organization,
                workspace=getattr(template, "workspace", None),
            )

            # Also update the template's config to match the restored version
            template.config = source_version.config_snapshot or {}
            template.criteria = source_version.criteria or ""
            template.model = source_version.model or ""
            template.save(update_fields=["config", "criteria", "model", "updated_at"])

            return self._gm.success_response(
                {
                    "id": str(new_version.id),
                    "version_number": new_version.version_number,
                    "is_default": new_version.is_default,
                    "restored_from": source_version.version_number,
                }
            )

        except Exception as e:
            logger.error(
                f"Error in RestoreVersionView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


def _validate_child_matches_axis(child_template, axis: str) -> None:
    """
    Raise ValueError if the child eval does not fit the composite's axis.

    Axis semantics:
      - pass_fail: child normalizes to a pass/fail boolean
      - percentage: child normalizes to a 0-1 float
      - choices: child has labelled choice scores
      - code: child is a code eval (eval_type == "code")

    A composite locks all children to one axis so aggregation numbers are
    interpretable (min as safety gate, pass_rate, weighted_avg etc.).
    """
    if not axis:
        return  # axis not set → legacy composite, skip enforcement

    cname = getattr(child_template, "name", "?")
    eval_type = getattr(child_template, "eval_type", "llm")
    output_norm = getattr(child_template, "output_type_normalized", None)
    choice_scores = getattr(child_template, "choice_scores", None)

    # Older / code-created templates may not have output_type_normalized set.
    # Derive it from config["output"] as a fallback so the axis check doesn't
    # incorrectly reject a Pass/Fail code eval.
    if not output_norm:
        _config_output = (getattr(child_template, "config", None) or {}).get(
            "output", ""
        )
        _output_map = {
            "Pass/Fail": "pass_fail",
            "score": "percentage",
            "choices": "choices",
        }
        output_norm = _output_map.get(_config_output)

    if axis == "code":
        if eval_type != "code":
            raise ValueError(
                f"Child '{cname}' is not a code eval. "
                f"This composite only accepts Code evals."
            )
        return

    if axis == "choices":
        if not choice_scores or not isinstance(choice_scores, dict):
            raise ValueError(
                f"Child '{cname}' does not have labelled choice scores. "
                f"This composite only accepts Choices evals."
            )
        return

    if axis == "pass_fail":
        if output_norm != "pass_fail":
            raise ValueError(
                f"Child '{cname}' is not a Pass/Fail eval. "
                f"This composite only accepts Pass/Fail evals."
            )
        return

    if axis == "percentage":
        if output_norm != "percentage":
            raise ValueError(
                f"Child '{cname}' is not a Score eval. "
                f"This composite only accepts Score evals."
            )
        return

    raise ValueError(f"Unknown composite child axis: {axis}")


class CompositeEvalCreateView(APIView):
    """
    POST /model-hub/eval-templates/create-composite/

    Create a composite eval from a list of existing eval template IDs.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        import re

        from model_hub.models.evals_metric import CompositeEvalChild
        from model_hub.types import (
            CompositeChildItem,
            CompositeCreateRequest,
            CompositeCreateResponse,
        )
        from model_hub.utils.eval_list import derive_eval_type, infer_composite_eval_type

        try:
            try:
                req = CompositeCreateRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Validate name
            cleaned_name = req.name.strip()
            if not re.match(r"^[a-z0-9_-]+$", cleaned_name):
                return self._gm.bad_request(
                    "Name can only contain lowercase letters, numbers, hyphens, or underscores."
                )

            # Check uniqueness
            if EvalTemplate.objects.filter(
                name=cleaned_name, organization=organization, deleted=False
            ).exists():
                return self._gm.bad_request(
                    "An evaluation with this name already exists."
                )

            # Verify all child templates exist and are accessible
            # System evals are accessible to all; user evals must be in same org
            children = list(
                EvalTemplate.no_workspace_objects.filter(
                    id__in=req.child_template_ids, deleted=False
                ).filter(
                    Q(owner=OwnerChoices.SYSTEM.value)
                    | Q(owner=OwnerChoices.USER.value, organization=organization)
                )
            )
            if len(children) != len(req.child_template_ids):
                return self._gm.bad_request(
                    "One or more child template IDs are invalid or not accessible."
                )

            # Validate aggregation_function
            from model_hub.types import AGGREGATION_FUNCTIONS, COMPOSITE_CHILD_AXES

            if req.aggregation_function not in AGGREGATION_FUNCTIONS:
                return self._gm.bad_request(
                    f"Invalid aggregation_function. Must be one of: {', '.join(AGGREGATION_FUNCTIONS)}"
                )

            # Validate composite_child_axis (empty string = legacy/unset, skipped)
            if (
                req.composite_child_axis
                and req.composite_child_axis not in COMPOSITE_CHILD_AXES
            ):
                return self._gm.bad_request(
                    f"Invalid composite_child_axis. Must be one of: "
                    f"{', '.join(COMPOSITE_CHILD_AXES)}"
                )

            # Block nested composites
            for child in children:
                if child.template_type == "composite":
                    return self._gm.bad_request(
                        "Composite evals cannot contain other composite evals."
                    )

            # Enforce homogeneity — every child must match the axis.
            # _validate_child_matches_axis is a no-op if axis is empty.
            if req.composite_child_axis:
                for child in children:
                    try:
                        _validate_child_matches_axis(child, req.composite_child_axis)
                    except ValueError as ve:
                        return self._gm.bad_request(str(ve))

            # Create the composite parent template
            parent = EvalTemplate.objects.create(
                name=cleaned_name,
                organization=organization,
                owner=OwnerChoices.USER.value,
                eval_tags=req.tags or [],
                config={},
                description=req.description or "",
                template_type="composite",
                eval_type=infer_composite_eval_type(
                    derive_eval_type(child) for child in children
                ),
                visible_ui=True,
                aggregation_enabled=req.aggregation_enabled,
                aggregation_function=req.aggregation_function,
                composite_child_axis=req.composite_child_axis,
            )

            # Create child links with optional weights
            child_items = []
            child_map = {str(c.id): c for c in children}
            weights = req.child_weights or {}
            for i, child_id in enumerate(req.child_template_ids):
                child = child_map[child_id]
                weight = weights.get(child_id, 1.0)
                CompositeEvalChild.objects.create(
                    parent=parent,
                    child=child,
                    order=i,
                    weight=weight,
                )
                child_items.append(
                    CompositeChildItem(
                        child_id=str(child.id),
                        child_name=child.name,
                        order=i,
                        eval_type=derive_eval_type(child),
                        weight=weight,
                    )
                )

            # Create initial version (V1) so created_by is tracked
            from model_hub.models.evals_metric import EvalTemplateVersion

            workspace = getattr(request, "workspace", None)
            try:
                EvalTemplateVersion.objects.create_version(
                    eval_template=parent,
                    prompt_messages=[],
                    config_snapshot={},
                    criteria=req.description or "",
                    model="",
                    user=request.user,
                    organization=organization,
                    workspace=workspace,
                )
            except Exception as ver_err:
                logger.warning(f"Failed to create V1 for composite: {ver_err}")

            response = CompositeCreateResponse(
                id=str(parent.id),
                name=parent.name,
                aggregation_enabled=parent.aggregation_enabled,
                aggregation_function=parent.aggregation_function,
                composite_child_axis=parent.composite_child_axis,
                children=[c.model_dump() for c in child_items],
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in CompositeEvalCreateView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class CompositeEvalDetailView(APIView):
    """
    GET /model-hub/eval-templates/<id>/composite/

    Get composite eval detail with its children.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import CompositeEvalChild
        from model_hub.types import CompositeChildItem, CompositeDetailResponse
        from model_hub.utils.eval_list import derive_eval_type

        try:
            try:
                parent = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False, template_type="composite"
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Composite eval template not found.")

            children = (
                CompositeEvalChild.objects.filter(parent=parent, deleted=False)
                .select_related("child", "pinned_version")
                .order_by("order")
            )

            child_items = []
            for link in children:
                child_cfg = link.child.config or {}
                child_required = list(child_cfg.get("required_keys") or [])
                child_items.append(
                    CompositeChildItem(
                        child_id=str(link.child_id),
                        child_name=link.child.name,
                        order=link.order,
                        eval_type=derive_eval_type(link.child),
                        pinned_version_id=(
                            str(link.pinned_version_id)
                            if link.pinned_version_id
                            else None
                        ),
                        pinned_version_number=(
                            link.pinned_version.version_number
                            if link.pinned_version
                            else None
                        ),
                        weight=link.weight,
                        required_keys=child_required,
                    )
                )

            response = CompositeDetailResponse(
                id=str(parent.id),
                name=parent.name,
                description=parent.description or "",
                aggregation_enabled=parent.aggregation_enabled,
                aggregation_function=parent.aggregation_function,
                composite_child_axis=parent.composite_child_axis or "",
                children=[c.model_dump() for c in child_items],
                tags=parent.eval_tags or [],
                created_at=parent.created_at.isoformat() if parent.created_at else "",
                updated_at=parent.updated_at.isoformat() if parent.updated_at else "",
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in CompositeEvalDetailView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))

    def patch(self, request, template_id, *args, **kwargs):
        """PATCH — partial update of a composite eval.

        Supported fields (all optional):
          name, description, tags,
          aggregation_enabled, aggregation_function,
          child_template_ids (replaces the child list),
          child_weights (map of child_id -> weight).
        """
        import re

        from model_hub.models.evals_metric import CompositeEvalChild
        from model_hub.types import (
            AGGREGATION_FUNCTIONS,
            COMPOSITE_CHILD_AXES,
            CompositeChildItem,
            CompositeDetailResponse,
            CompositeUpdateRequest,
        )
        from model_hub.utils.eval_list import derive_eval_type, infer_composite_eval_type

        try:
            try:
                req = CompositeUpdateRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Fetch parent composite — must exist and be a composite
            try:
                parent = EvalTemplate.objects.get(
                    id=template_id,
                    deleted=False,
                    template_type="composite",
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Composite eval template not found.")

            # Only users in the same org may edit a composite
            if parent.organization_id != organization.id:
                return self._gm.not_found("Composite eval template not found.")

            # Validate aggregation_function if provided
            if (
                req.aggregation_function is not None
                and req.aggregation_function not in AGGREGATION_FUNCTIONS
            ):
                return self._gm.bad_request(
                    f"Invalid aggregation_function. Must be one of: "
                    f"{', '.join(AGGREGATION_FUNCTIONS)}"
                )

            # Validate composite_child_axis if provided (empty string = clear/legacy)
            if (
                req.composite_child_axis
                and req.composite_child_axis not in COMPOSITE_CHILD_AXES
            ):
                return self._gm.bad_request(
                    f"Invalid composite_child_axis. Must be one of: "
                    f"{', '.join(COMPOSITE_CHILD_AXES)}"
                )

            # Validate & update name
            if req.name is not None:
                cleaned_name = req.name.strip()
                if not re.match(r"^[a-z0-9_-]+$", cleaned_name):
                    return self._gm.bad_request(
                        "Name can only contain lowercase letters, numbers, "
                        "hyphens, or underscores."
                    )
                # Name uniqueness — exclude self
                if (
                    EvalTemplate.objects.filter(
                        name=cleaned_name,
                        organization=organization,
                        deleted=False,
                    )
                    .exclude(id=parent.id)
                    .exists()
                ):
                    return self._gm.bad_request(
                        "An evaluation with this name already exists."
                    )
                parent.name = cleaned_name

            # Determine the effective axis for this update.
            effective_axis = (
                req.composite_child_axis
                if req.composite_child_axis is not None
                else (parent.composite_child_axis or "")
            )

            # If the axis is changing and the caller did not supply a new
            # child list, every current child must still fit the new axis.
            # Check this BEFORE mutating anything so we fail cleanly on 400.
            if (
                req.composite_child_axis is not None
                and req.composite_child_axis != (parent.composite_child_axis or "")
                and req.child_template_ids is None
            ):
                existing_links = CompositeEvalChild.objects.filter(
                    parent=parent, deleted=False
                ).select_related("child")
                for link in existing_links:
                    try:
                        _validate_child_matches_axis(
                            link.child, req.composite_child_axis
                        )
                    except ValueError as ve:
                        return self._gm.bad_request(
                            f"Cannot switch to '{req.composite_child_axis}' axis: {ve}"
                        )

            # Update simple fields
            if req.description is not None:
                parent.description = req.description
            if req.tags is not None:
                parent.eval_tags = req.tags
            if req.aggregation_enabled is not None:
                parent.aggregation_enabled = req.aggregation_enabled
            if req.aggregation_function is not None:
                parent.aggregation_function = req.aggregation_function
            if req.composite_child_axis is not None:
                parent.composite_child_axis = req.composite_child_axis

            # Replace child list if provided
            if req.child_template_ids is not None:
                # Verify all child templates are accessible
                child_qs = list(
                    EvalTemplate.no_workspace_objects.filter(
                        id__in=req.child_template_ids, deleted=False
                    ).filter(
                        Q(owner=OwnerChoices.SYSTEM.value)
                        | Q(
                            owner=OwnerChoices.USER.value,
                            organization=organization,
                        )
                    )
                )
                if len(child_qs) != len(req.child_template_ids):
                    return self._gm.bad_request(
                        "One or more child template IDs are invalid or not accessible."
                    )
                # Prevent nested composites
                for c in child_qs:
                    if c.template_type == "composite":
                        return self._gm.bad_request(
                            "Composite evals cannot contain other composite evals."
                        )

                # Enforce homogeneity against the effective axis
                if effective_axis:
                    for c in child_qs:
                        try:
                            _validate_child_matches_axis(c, effective_axis)
                        except ValueError as ve:
                            return self._gm.bad_request(str(ve))

                parent.eval_type = infer_composite_eval_type(
                    derive_eval_type(child) for child in child_qs
                )
                # Soft-delete existing children links, then recreate
                CompositeEvalChild.objects.filter(parent=parent, deleted=False).update(
                    deleted=True
                )

                child_map = {str(c.id): c for c in child_qs}
                weights = req.child_weights or {}
                for i, child_id in enumerate(req.child_template_ids):
                    child = child_map[child_id]
                    CompositeEvalChild.objects.create(
                        parent=parent,
                        child=child,
                        order=i,
                        weight=weights.get(child_id, 1.0),
                    )
            elif req.child_weights is not None:
                existing_links = CompositeEvalChild.objects.filter(
                    parent=parent, deleted=False
                )
                for link in existing_links:
                    cid = str(link.child_id)
                    if cid in req.child_weights:
                        link.weight = req.child_weights[cid]
                        link.save(update_fields=["weight"])

            parent.save()

            # Re-fetch children and return the updated detail response.
            links = list(
                CompositeEvalChild.objects.filter(parent=parent, deleted=False)
                .select_related("child", "pinned_version")
                .order_by("order")
            )

            # Create a new version snapshot for the composite
            from model_hub.models.evals_metric import EvalTemplateVersion

            config_snapshot = {
                "aggregation_enabled": parent.aggregation_enabled,
                "aggregation_function": parent.aggregation_function,
                "composite_child_axis": parent.composite_child_axis or "",
                "children": [
                    {
                        "child_id": str(link.child_id),
                        "child_name": link.child.name,
                        "order": link.order,
                        "weight": link.weight,
                        "pinned_version_id": (
                            str(link.pinned_version_id)
                            if link.pinned_version_id
                            else None
                        ),
                    }
                    for link in links
                ],
            }
            workspace = getattr(parent, "workspace", None)
            new_version = EvalTemplateVersion.objects.create_version(
                eval_template=parent,
                config_snapshot=config_snapshot,
                criteria=parent.description or "",
                model="",
                user=request.user,
                organization=organization,
                workspace=workspace,
            )
            child_items = [
                CompositeChildItem(
                    child_id=str(link.child_id),
                    child_name=link.child.name,
                    order=link.order,
                    eval_type=derive_eval_type(link.child),
                    pinned_version_id=(
                        str(link.pinned_version_id) if link.pinned_version_id else None
                    ),
                    pinned_version_number=(
                        link.pinned_version.version_number
                        if link.pinned_version
                        else None
                    ),
                    weight=link.weight,
                )
                for link in links
            ]

            response = CompositeDetailResponse(
                id=str(parent.id),
                name=parent.name,
                description=parent.description or "",
                aggregation_enabled=parent.aggregation_enabled,
                aggregation_function=parent.aggregation_function,
                composite_child_axis=parent.composite_child_axis or "",
                children=[c.model_dump() for c in child_items],
                tags=parent.eval_tags or [],
                created_at=parent.created_at.isoformat() if parent.created_at else "",
                updated_at=parent.updated_at.isoformat() if parent.updated_at else "",
                version_number=new_version.version_number,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in CompositeEvalDetailView.patch: "
                f"{str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


def _persist_composite_evaluation(
    *,
    user,
    org,
    workspace,
    parent_template,
    child_links,
    outcome,
    mapping=None,
    model=None,
):
    """Create 1 parent Evaluation + N child Evaluation records.

    Used by the one-shot composite execute endpoint so results persist
    in the same shape as the dataset/experiment runner writes them.
    Returns the parent evaluation ID or None on failure.
    """
    from model_hub.models.evaluation import Evaluation, StatusChoices

    try:
        parent_row = Evaluation.objects.create(
            user=user,
            organization=org,
            workspace=workspace,
            eval_template=parent_template,
            model_name=model,
            status=StatusChoices.COMPLETED,
            input_data={"mapping": mapping} if mapping else {},
            eval_config={
                "composite": True,
                "aggregation_enabled": parent_template.aggregation_enabled,
                "aggregation_function": parent_template.aggregation_function,
            },
            data={
                "aggregate_score": outcome.aggregate_score,
                "aggregate_pass": outcome.aggregate_pass,
                "summary": outcome.summary,
            },
            reason=outcome.summary or "",
            value=(
                outcome.aggregate_score if parent_template.aggregation_enabled else None
            ),
        )

        child_template_map = {str(link.child_id): link.child for link in child_links}
        for cr in outcome.child_results:
            child_template = child_template_map.get(cr.child_id)
            if not child_template:
                continue
            Evaluation.objects.create(
                user=user,
                organization=org,
                workspace=workspace,
                eval_template=child_template,
                parent_evaluation=parent_row,
                model_name=model,
                status=(
                    StatusChoices.COMPLETED
                    if cr.status == "completed"
                    else StatusChoices.FAILED
                ),
                input_data={"mapping": mapping} if mapping else {},
                eval_config={
                    "child_of": str(parent_template.id),
                    "order": cr.order,
                },
                data={
                    "score": cr.score,
                    "output": cr.output,
                    "output_type": cr.output_type,
                    "weight": cr.weight,
                },
                reason=cr.reason or "",
                value=cr.score,
                error_message=cr.error or "",
            )

        return str(parent_row.id)
    except Exception:
        logger.exception("Failed to persist composite Evaluation records")
        return None


class CompositeEvalExecuteView(APIView):
    """
    POST /model-hub/eval-templates/<template_id>/composite/execute/

    Execute all child evals in a composite and optionally aggregate results.
    Thin wrapper around `execute_composite_children_sync` — the same helper
    the dataset/experiment `CompositeEvaluationRunner` uses, so aggregation
    semantics stay consistent across surfaces.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import CompositeEvalChild
        from model_hub.types import CompositeExecuteRequest, CompositeExecuteResponse
        from model_hub.utils.composite_execution import (
            execute_composite_children_sync,
        )

        try:
            try:
                req = CompositeExecuteRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            org = getattr(request, "organization", None) or request.user.organization

            try:
                parent = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False, template_type="composite"
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Composite eval template not found.")

            child_links = list(
                CompositeEvalChild.objects.filter(parent=parent, deleted=False)
                .select_related("child", "pinned_version")
                .order_by("order")
            )
            if not child_links:
                return self._gm.bad_request("Composite eval has no children.")

            # Defence in depth — if a child has been edited since it was added
            # to the composite, reject the run with a clear message rather than
            # silently aggregating mismatched score shapes.
            if parent.composite_child_axis:
                for link in child_links:
                    try:
                        _validate_child_matches_axis(
                            link.child, parent.composite_child_axis
                        )
                    except ValueError as ve:
                        return self._gm.bad_request(
                            f"Composite cannot run: {ve} "
                            f"Edit the composite to remove or replace this child."
                        )

            workspace = getattr(request, "workspace", None)

            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=child_links,
                mapping=req.mapping,
                config=req.config,
                org=org,
                workspace=workspace,
                model=req.model,
                input_data_types=req.input_data_types,
                row_context=req.row_context,
                span_context=req.span_context,
                trace_context=req.trace_context,
                session_context=req.session_context,
                call_context=req.call_context,
                error_localizer=req.error_localizer,
                source="composite_eval",
            )

            # Persist Evaluation records: 1 parent + N children
            evaluation_id = _persist_composite_evaluation(
                user=request.user,
                org=org,
                workspace=workspace,
                parent_template=parent,
                child_links=child_links,
                outcome=outcome,
                mapping=req.mapping,
                model=req.model,
            )

            completed = sum(
                1 for cr in outcome.child_results if cr.status == "completed"
            )
            failed = sum(1 for cr in outcome.child_results if cr.status == "failed")

            response = CompositeExecuteResponse(
                composite_id=str(parent.id),
                composite_name=parent.name,
                aggregation_enabled=parent.aggregation_enabled,
                aggregation_function=(
                    parent.aggregation_function if parent.aggregation_enabled else None
                ),
                aggregate_score=outcome.aggregate_score,
                aggregate_pass=outcome.aggregate_pass,
                children=[cr.model_dump() for cr in outcome.child_results],
                summary=outcome.summary,
                error_localizer_results=outcome.error_localizer_results,
                total_children=len(outcome.child_results),
                completed_children=completed,
                failed_children=failed,
                evaluation_id=evaluation_id,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in CompositeEvalExecuteView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class CompositeEvalAdhocExecuteView(APIView):
    """
    POST /model-hub/eval-templates/composite/execute-adhoc/

    Execute a composite eval configuration without persisting it. Used by
    the eval create page so users can test a composite (selected children +
    aggregation settings) before clicking Save. Builds an unsaved parent
    template and unsaved child links in memory and reuses
    `execute_composite_children_sync` so semantics match the persisted path.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from model_hub.models.evals_metric import CompositeEvalChild
        from model_hub.types import (
            AGGREGATION_FUNCTIONS,
            COMPOSITE_CHILD_AXES,
            CompositeAdhocExecuteRequest,
            CompositeExecuteResponse,
        )
        from model_hub.utils.composite_execution import (
            execute_composite_children_sync,
        )

        try:
            try:
                req = CompositeAdhocExecuteRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            if req.aggregation_function not in AGGREGATION_FUNCTIONS:
                return self._gm.bad_request(
                    f"Invalid aggregation_function. Must be one of: "
                    f"{', '.join(AGGREGATION_FUNCTIONS)}"
                )
            if (
                req.composite_child_axis
                and req.composite_child_axis not in COMPOSITE_CHILD_AXES
            ):
                return self._gm.bad_request(
                    f"Invalid composite_child_axis. Must be one of: "
                    f"{', '.join(COMPOSITE_CHILD_AXES)}"
                )

            org = getattr(request, "organization", None) or request.user.organization

            # Same accessibility rule as CompositeEvalCreateView: system evals
            # are visible to everyone, user evals must belong to the caller's org.
            children_qs = EvalTemplate.no_workspace_objects.filter(
                id__in=req.child_template_ids, deleted=False
            ).filter(
                Q(owner=OwnerChoices.SYSTEM.value)
                | Q(owner=OwnerChoices.USER.value, organization=org)
            )
            children_by_id = {str(c.id): c for c in children_qs}
            if len(children_by_id) != len(set(req.child_template_ids)):
                return self._gm.bad_request(
                    "One or more child template IDs are invalid or not accessible."
                )

            if req.composite_child_axis:
                for child in children_by_id.values():
                    try:
                        _validate_child_matches_axis(child, req.composite_child_axis)
                    except ValueError as ve:
                        return self._gm.bad_request(str(ve))

            # Build an unsaved parent template carrying the aggregation config
            # the runner reads. Never .save() this — it must stay in-memory.
            parent = EvalTemplate(
                name="(adhoc-composite)",
                organization=org,
                owner=OwnerChoices.USER.value,
                template_type="composite",
                aggregation_enabled=req.aggregation_enabled,
                aggregation_function=req.aggregation_function,
                composite_child_axis=req.composite_child_axis,
                pass_threshold=req.pass_threshold,
                config={},
            )

            weights = req.child_weights or {}
            child_links: list[CompositeEvalChild] = []
            for i, child_id in enumerate(req.child_template_ids):
                child = children_by_id[child_id]
                # Unsaved link object — execute_composite_children_sync only
                # reads .child, .child_id, .order, .weight, .pinned_version.
                link = CompositeEvalChild(
                    parent=parent,
                    child=child,
                    order=i,
                    weight=float(weights.get(child_id, 1.0)),
                )
                child_links.append(link)

            outcome = execute_composite_children_sync(
                parent=parent,
                child_links=child_links,
                mapping=req.mapping,
                config=req.config,
                org=org,
                workspace=getattr(request, "workspace", None),
                model=req.model,
                input_data_types=req.input_data_types,
                row_context=req.row_context,
                span_context=req.span_context,
                trace_context=req.trace_context,
                session_context=req.session_context,
                call_context=req.call_context,
                error_localizer=req.error_localizer,
                source="composite_eval_adhoc",
            )

            completed = sum(
                1 for cr in outcome.child_results if cr.status == "completed"
            )
            failed = sum(1 for cr in outcome.child_results if cr.status == "failed")

            response = CompositeExecuteResponse(
                composite_id="",
                composite_name=parent.name,
                aggregation_enabled=parent.aggregation_enabled,
                aggregation_function=(
                    parent.aggregation_function if parent.aggregation_enabled else None
                ),
                aggregate_score=outcome.aggregate_score,
                aggregate_pass=outcome.aggregate_pass,
                children=[cr.model_dump() for cr in outcome.child_results],
                summary=outcome.summary,
                error_localizer_results=outcome.error_localizer_results,
                total_children=len(outcome.child_results),
                completed_children=completed,
                failed_children=failed,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in CompositeEvalAdhocExecuteView: {str(e)}\n"
                f"{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthListView(APIView):
    """GET /model-hub/eval-templates/<id>/ground-truth/"""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import GroundTruthItem, GroundTruthListResponse

        try:
            try:
                EvalTemplate.no_workspace_objects.get(id=template_id, deleted=False)
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            gts = EvalGroundTruth.objects.filter(
                eval_template_id=template_id, deleted=False
            ).order_by("-created_at")

            items = [
                GroundTruthItem(
                    id=str(gt.id),
                    name=gt.name,
                    description=gt.description or "",
                    file_name=gt.file_name or "",
                    columns=gt.columns or [],
                    row_count=gt.row_count,
                    variable_mapping=gt.variable_mapping,
                    role_mapping=gt.role_mapping,
                    embedding_status=gt.embedding_status,
                    embedded_row_count=gt.embedded_row_count,
                    storage_type=gt.storage_type,
                    created_at=gt.created_at.isoformat() if gt.created_at else "",
                )
                for gt in gts
            ]

            response = GroundTruthListResponse(
                template_id=str(template_id),
                items=[i.model_dump() for i in items],
                total=len(items),
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in GroundTruthListView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthUploadView(APIView):
    """
    POST /model-hub/eval-templates/<id>/ground-truth/upload/

    Supports two modes:
    1. JSON body: { name, columns, data, ... }
    2. Multipart file upload: file (CSV/XLS/XLSX/JSON) + name field
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import GroundTruthUploadResponse

        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            uploaded_file = request.FILES.get("file")

            if uploaded_file:
                # --- File upload mode ---
                from model_hub.utils.ground_truth_parser import (
                    MAX_FILE_SIZE_BYTES,
                    parse_ground_truth_file,
                )

                if uploaded_file.size > MAX_FILE_SIZE_BYTES:
                    return self._gm.bad_request("File exceeds maximum size of 50MB.")

                name = request.data.get("name", uploaded_file.name.rsplit(".", 1)[0])
                description = request.data.get("description", "")

                try:
                    columns, data = parse_ground_truth_file(
                        uploaded_file, uploaded_file.name
                    )
                except ValueError as e:
                    return self._gm.bad_request(str(e))

                file_name = uploaded_file.name
                variable_mapping = None
                role_mapping = None

                # Parse optional JSON fields from multipart
                vm_raw = request.data.get("variable_mapping")
                if vm_raw and isinstance(vm_raw, str):
                    try:
                        variable_mapping = json.loads(vm_raw)
                    except json.JSONDecodeError:
                        pass
                elif isinstance(vm_raw, dict):
                    variable_mapping = vm_raw

                rm_raw = request.data.get("role_mapping")
                if rm_raw and isinstance(rm_raw, str):
                    try:
                        role_mapping = json.loads(rm_raw)
                    except json.JSONDecodeError:
                        pass
                elif isinstance(rm_raw, dict):
                    role_mapping = rm_raw
            else:
                # --- JSON body mode (backwards compatible) ---
                from model_hub.types import GroundTruthUploadRequest

                try:
                    req = GroundTruthUploadRequest(**request.data)
                except Exception as e:
                    from tfc.utils.errors import format_request_error

                    return self._gm.bad_request(format_request_error(e))

                if not req.columns:
                    return self._gm.bad_request("Columns list is required.")

                name = req.name
                description = req.description
                file_name = req.file_name
                columns = req.columns
                data = req.data
                variable_mapping = req.variable_mapping
                role_mapping = req.role_mapping

            gt = EvalGroundTruth.objects.create(
                eval_template=template,
                name=name,
                description=description,
                file_name=file_name,
                columns=columns,
                data=data,
                row_count=len(data),
                variable_mapping=variable_mapping,
                role_mapping=role_mapping,
                embedding_status="pending",
                organization=organization,
                workspace=getattr(request, "workspace", None),
            )

            response = GroundTruthUploadResponse(
                id=str(gt.id),
                name=gt.name,
                row_count=gt.row_count,
                columns=gt.columns,
                embedding_status=gt.embedding_status,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in GroundTruthUploadView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthMappingView(APIView):
    """PUT /model-hub/ground-truth/<id>/mapping/"""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def put(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import VariableMappingRequest

        try:
            try:
                req = VariableMappingRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            gt.variable_mapping = req.variable_mapping
            gt.save(update_fields=["variable_mapping", "updated_at"])

            return self._gm.success_response(
                {"id": str(gt.id), "variable_mapping": gt.variable_mapping}
            )

        except Exception as e:
            logger.error(
                f"Error in GroundTruthMappingView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthRoleMappingView(APIView):
    """PUT /model-hub/ground-truth/<id>/role-mapping/"""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def put(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import RoleMappingRequest

        try:
            try:
                req = RoleMappingRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            valid_roles = {"input", "expected_output", "score", "reasoning"}
            invalid = set(req.role_mapping.keys()) - valid_roles
            if invalid:
                return self._gm.bad_request(
                    f"Invalid roles: {invalid}. Valid roles: {valid_roles}"
                )

            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            # Validate that mapped columns exist in the dataset
            for role, col in req.role_mapping.items():
                if col not in (gt.columns or []):
                    return self._gm.bad_request(
                        f"Column '{col}' (mapped to role '{role}') not found in dataset columns: {gt.columns}"
                    )

            gt.role_mapping = req.role_mapping
            gt.save(update_fields=["role_mapping", "updated_at"])

            return self._gm.success_response(
                {
                    "id": str(gt.id),
                    "role_mapping": gt.role_mapping,
                    "embedding_status": gt.embedding_status,
                }
            )

        except Exception as e:
            logger.error(
                f"Error in GroundTruthRoleMappingView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthDataView(APIView):
    """GET /model-hub/ground-truth/<id>/data/?page=1&page_size=50"""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import GroundTruthDataResponse

        try:
            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(100, max(1, int(request.query_params.get("page_size", 50))))
            total_rows = gt.row_count
            total_pages = math.ceil(total_rows / page_size) if total_rows > 0 else 1

            start = (page - 1) * page_size
            end = start + page_size
            rows = (gt.data or [])[start:end]

            response = GroundTruthDataResponse(
                id=str(gt.id),
                page=page,
                page_size=page_size,
                total_rows=total_rows,
                total_pages=total_pages,
                columns=gt.columns or [],
                rows=rows,
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in GroundTruthDataView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthStatusView(APIView):
    """GET /model-hub/ground-truth/<id>/status/"""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import GroundTruthStatusResponse

        try:
            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            total = gt.row_count or 0
            embedded = gt.embedded_row_count or 0
            progress = (embedded / total * 100) if total > 0 else 0.0

            response = GroundTruthStatusResponse(
                id=str(gt.id),
                embedding_status=gt.embedding_status,
                embedded_row_count=embedded,
                total_rows=total,
                progress_percent=round(progress, 1),
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in GroundTruthStatusView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthDeleteView(APIView):
    """DELETE /model-hub/ground-truth/<id>/"""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def delete(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth

        try:
            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            gt.deleted = True
            gt.save(update_fields=["deleted", "updated_at"])

            # Also soft-delete embeddings
            gt.embeddings.update(ground_truth=gt)

            return self._gm.success_response({"deleted": True, "id": str(gt.id)})

        except Exception as e:
            logger.error(
                f"Error in GroundTruthDeleteView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthConfigView(APIView):
    """
    GET/PUT /model-hub/eval-templates/<id>/ground-truth-config/

    Manages ground truth configuration on the eval template's config JSONField.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        try:
            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            config = template.config or {}
            gt_config = config.get(
                "ground_truth",
                {
                    "enabled": False,
                    "ground_truth_id": None,
                    "mode": "auto",
                    "max_examples": 3,
                    "similarity_threshold": 0.7,
                    "injection_format": "structured",
                },
            )

            return self._gm.success_response({"ground_truth": gt_config})

        except Exception as e:
            logger.error(
                f"Error in GroundTruthConfigView.get: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))

    def put(self, request, template_id, *args, **kwargs):
        from model_hub.types import GroundTruthConfigRequest

        try:
            try:
                req = GroundTruthConfigRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            # Validate ground_truth_id exists if provided
            if req.ground_truth_id:
                from model_hub.models.evals_metric import EvalGroundTruth

                if not EvalGroundTruth.objects.filter(
                    id=req.ground_truth_id, eval_template=template, deleted=False
                ).exists():
                    return self._gm.bad_request(
                        "Ground truth dataset not found or does not belong to this eval template."
                    )

            config = template.config or {}
            config["ground_truth"] = {
                "enabled": req.enabled,
                "ground_truth_id": req.ground_truth_id,
                "mode": req.mode,
                "max_examples": req.max_examples,
                "similarity_threshold": req.similarity_threshold,
                "injection_format": req.injection_format,
            }
            template.config = config
            template.save(update_fields=["config", "updated_at"])

            return self._gm.success_response({"ground_truth": config["ground_truth"]})

        except Exception as e:
            logger.error(
                f"Error in GroundTruthConfigView.put: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthSearchView(APIView):
    """POST /model-hub/ground-truth/<id>/search/ — test retrieval with a query."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth
        from model_hub.types import GroundTruthSearchRequest
        from model_hub.utils.ground_truth_retrieval import (
            generate_embedding,
            retrieve_similar_examples,
        )

        try:
            try:
                req = GroundTruthSearchRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            if gt.embedding_status != "completed":
                return self._gm.bad_request(
                    f"Embeddings not ready. Status: {gt.embedding_status}. "
                    "Wait for embedding generation to complete."
                )

            query_embedding = generate_embedding(req.query)

            results = retrieve_similar_examples(
                ground_truth_id=str(gt.id),
                query_embedding=query_embedding,
                max_examples=req.max_results,
                similarity_threshold=0.0,  # Return all for testing, let user see scores
            )

            return self._gm.success_response(
                {
                    "query": req.query,
                    "results": results,
                    "total": len(results),
                }
            )

        except Exception as e:
            logger.error(
                f"Error in GroundTruthSearchView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class GroundTruthTriggerEmbeddingView(APIView):
    """POST /model-hub/ground-truth/<id>/embed/ — trigger embedding generation."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, ground_truth_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalGroundTruth

        try:
            try:
                gt = EvalGroundTruth.objects.get(id=ground_truth_id, deleted=False)
            except EvalGroundTruth.DoesNotExist:
                return self._gm.not_found("Ground truth not found.")

            if gt.embedding_status == "processing":
                return self._gm.bad_request(
                    "Embedding generation is already in progress."
                )

            if gt.row_count == 0:
                return self._gm.bad_request("No data rows to embed.")

            # Reset status
            gt.embedding_status = "pending"
            gt.embedded_row_count = 0
            gt.save(
                update_fields=["embedding_status", "embedded_row_count", "updated_at"]
            )

            # Trigger async workflow
            import asyncio

            from tfc.temporal.ground_truth.client import trigger_embedding_generation

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're inside an async context, schedule the coroutine
                    asyncio.ensure_future(trigger_embedding_generation(str(gt.id)))
                else:
                    loop.run_until_complete(trigger_embedding_generation(str(gt.id)))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(trigger_embedding_generation(str(gt.id)))

            return self._gm.success_response(
                {
                    "id": str(gt.id),
                    "embedding_status": "pending",
                    "message": "Embedding generation triggered.",
                }
            )

        except Exception as e:
            logger.error(
                f"Error in GroundTruthTriggerEmbeddingView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalUsageStatsView(APIView):
    """
    GET /model-hub/eval-templates/<id>/usage/

    Returns usage stats, chart data, and paginated eval logs.
    Query params: page (0-based), page_size, period (30m|6h|1d|7d|30d|90d)
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    PERIOD_MAP = {
        "30m": timedelta(minutes=30),
        "6h": timedelta(hours=6),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "90d": timedelta(days=90),
    }

    def get(self, request, template_id, *args, **kwargs):
        try:
            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            # Parse query params
            page = int(request.GET.get("page", 0))
            page_size = min(int(request.GET.get("page_size", 25)), 100)
            period = request.GET.get("period", "30d")
            version_filter = request.GET.get("version", None)

            period_delta = self.PERIOD_MAP.get(period, timedelta(days=30))
            end_date = timezone.now()
            start_date = end_date - period_delta

            # Base queryset
            base_qs = APICallLog.objects.filter(
                organization=organization,
                source_id=str(template_id),
                deleted=False,
            )
            total_runs = base_qs.count()

            # Period-filtered queryset
            period_qs = base_qs.filter(created_at__gte=start_date)
            runs_period = period_qs.count()

            success_count = period_qs.filter(
                status=APICallStatusChoices.SUCCESS.value
            ).count()
            error_count = period_qs.filter(
                status=APICallStatusChoices.ERROR.value
            ).count()

            # Chart data — aggregate by time bucket
            from collections import defaultdict

            chart_data = []
            if runs_period > 0:
                # Pick bucket size based on period
                if period in ("30m", "6h", "1d"):
                    bucket_minutes = (
                        10 if period == "30m" else (60 if period == "6h" else 360)
                    )
                else:
                    bucket_minutes = 1440  # 1 day

                buckets_calls = defaultdict(int)
                buckets_latency = defaultdict(list)
                buckets_scores = defaultdict(list)
                buckets_pass = defaultdict(int)
                buckets_fail = defaultdict(int)

                for log in period_qs.values("created_at", "config", "status"):
                    ts = log["created_at"]
                    # Round to bucket
                    bucket_ts = ts.replace(
                        minute=(
                            (ts.minute // max(bucket_minutes, 1))
                            * min(bucket_minutes, 60)
                            if bucket_minutes < 1440
                            else 0
                        ),
                        second=0,
                        microsecond=0,
                    )
                    if bucket_minutes >= 1440:
                        bucket_ts = bucket_ts.replace(hour=0)
                    bucket_key = bucket_ts.isoformat()
                    buckets_calls[bucket_key] += 1

                    # Extract latency + score from config
                    config = log.get("config")
                    if isinstance(config, str):
                        try:
                            config = json.loads(config)
                        except Exception:
                            config = {}
                    if isinstance(config, dict):
                        duration = config.get("duration") or config.get("response_time")
                        if duration:
                            try:
                                buckets_latency[bucket_key].append(float(duration))
                            except (ValueError, TypeError):
                                pass

                        # Extract score/result from output
                        output = config.get("output", {})
                        if isinstance(output, dict):
                            is_composite = config.get("composite") is True
                            score = output.get("output")
                            if isinstance(score, (int, float)):
                                buckets_scores[bucket_key].append(float(score))
                                # Composite logs carry aggregate_pass
                                if is_composite:
                                    agg_pass = output.get("aggregate_pass")
                                    if agg_pass is True:
                                        buckets_pass[bucket_key] += 1
                                    elif agg_pass is False:
                                        buckets_fail[bucket_key] += 1
                            elif score in ("Passed", "Pass"):
                                buckets_pass[bucket_key] += 1
                                buckets_scores[bucket_key].append(1.0)
                            elif score in ("Failed", "Fail"):
                                buckets_fail[bucket_key] += 1
                                buckets_scores[bucket_key].append(0.0)

                # Zero-fill: generate all buckets in the range
                from datetime import datetime as _dt

                current_bucket = start_date.replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if bucket_minutes >= 1440:
                    current_bucket = current_bucket.replace(hour=0)
                all_bucket_keys = []
                while current_bucket <= end_date:
                    all_bucket_keys.append(current_bucket.isoformat())
                    if bucket_minutes >= 1440:
                        current_bucket += timedelta(days=1)
                    else:
                        current_bucket += timedelta(minutes=bucket_minutes)

                for ts_key in all_bucket_keys:
                    latencies = buckets_latency.get(ts_key, [])
                    scores = buckets_scores.get(ts_key, [])
                    avg_latency = sum(latencies) / len(latencies) if latencies else 0
                    avg_score = sum(scores) / len(scores) if scores else None
                    chart_data.append(
                        {
                            "timestamp": ts_key,
                            "calls": buckets_calls.get(ts_key, 0),
                            "avg_latency_ms": (
                                round(avg_latency * 1000)
                                if avg_latency < 100
                                else round(avg_latency)
                            ),
                            "avg_score": (
                                round(avg_score, 3) if avg_score is not None else None
                            ),
                            "pass_count": buckets_pass.get(ts_key, 0),
                            "fail_count": buckets_fail.get(ts_key, 0),
                        }
                    )

            # Paginated logs
            logs_qs = period_qs.order_by("-created_at")
            total_logs = logs_qs.count()
            logs_page = logs_qs[page * page_size : (page + 1) * page_size]

            # Batch-fetch feedbacks for this page's log IDs
            log_ids = [str(log.log_id) for log in logs_page]
            feedbacks_qs = Feedback.objects.filter(
                source_id__in=log_ids,
                organization=organization,
                deleted=False,
            ).order_by("-created_at")
            feedback_map = {}
            for fb in feedbacks_qs:
                if fb.source_id not in feedback_map:
                    feedback_map[fb.source_id] = {
                        "id": str(fb.id),
                        "value": fb.value,
                        "explanation": fb.explanation or "",
                        "action_type": fb.action_type or "",
                        "created_at": (
                            fb.created_at.isoformat() if fb.created_at else ""
                        ),
                        "user": fb.user.email if fb.user else "",
                    }

            log_items = []
            _skip_keys = {
                "call_type",
                "image_urls",
                "input_data_types",
                "config",
                "params",
                "model",
                "choices",
                "multi_choice",
                "mapping",
                "mappings",
                "source",
                "reference_id",
                "is_futureagi_eval",
                "required_keys",
                "error_localizer",
                "kb_id",
                "row_context",
            }

            for log in logs_page:
                config = log.config
                if isinstance(config, str):
                    try:
                        config = json.loads(config)
                    except Exception:
                        config = {}

                is_composite_log = (
                    isinstance(config, dict) and config.get("composite") is True
                )

                output_data = (
                    config.get("output", {}) if isinstance(config, dict) else {}
                )
                source = config.get("source", "") if isinstance(config, dict) else ""

                # Extract mapped input variables (the actual eval inputs)
                mappings = (
                    config.get("mappings", {}) if isinstance(config, dict) else {}
                )
                input_vars = {}
                if isinstance(mappings, dict):
                    for k, v in mappings.items():
                        if k not in _skip_keys and v is not None:
                            val_str = str(v) if not isinstance(v, (dict, list)) else ""
                            if not val_str or val_str.startswith("There seems to be"):
                                continue
                            # Truncate URLs to just show [image] or [url]
                            if val_str.startswith("http"):
                                val_str = (
                                    "[image]"
                                    if any(
                                        ext in val_str.lower()
                                        for ext in (
                                            ".png",
                                            ".jpg",
                                            ".jpeg",
                                            ".webp",
                                            ".gif",
                                            ".svg",
                                        )
                                    )
                                    else "[url]"
                                )
                            else:
                                val_str = val_str[:100]
                            input_vars[k] = val_str

                # Build input summary: "key1: val1, key2: val2"
                if input_vars:
                    input_str = ", ".join(
                        f"{k}: {v[:60]}" for k, v in list(input_vars.items())[:3]
                    )
                else:
                    # Fallback to config.input
                    input_data = (
                        config.get("input", {}) if isinstance(config, dict) else {}
                    )
                    if isinstance(input_data, dict):
                        parts = []
                        for k, v in input_data.items():
                            if v and k not in _skip_keys:
                                parts.append(f"{k}: {str(v)[:60]}")
                        input_str = ", ".join(parts[:3])
                    elif isinstance(input_data, str):
                        input_str = input_data[:200]
                    else:
                        input_str = ""

                # Extract score and reason from output
                score = None
                reason = ""
                result_label = ""
                if isinstance(output_data, dict):
                    raw_output = output_data.get("output")
                    reason = output_data.get("reason", "")
                    if isinstance(raw_output, dict):
                        # Choice object
                        result_label = raw_output.get("label", "")
                        score = raw_output.get("score")
                    elif isinstance(raw_output, (int, float)):
                        score = raw_output
                    elif isinstance(raw_output, str):
                        result_label = raw_output
                        if raw_output in ("Passed", "Pass"):
                            score = 1.0
                        elif raw_output in ("Failed", "Fail"):
                            score = 0.0

                # Composite-specific: derive result label from aggregate_pass
                if is_composite_log and isinstance(output_data, dict):
                    agg_pass = output_data.get("aggregate_pass")
                    if agg_pass is True:
                        result_label = "Passed"
                    elif agg_pass is False:
                        result_label = "Failed"

                log_item = {
                    "id": str(log.log_id),
                    "input": input_str[:200],
                    "result": result_label,
                    "score": score,
                    "reason": ((reason[:150] + "...") if len(reason) > 150 else reason),
                    "status": log.status,
                    "source": source,
                    "created_at": (
                        log.created_at.isoformat() if log.created_at else ""
                    ),
                    "detail": {
                        "input_variables": input_vars or config.get("input", {}),
                        "output": output_data,
                        "mappings": mappings,
                        "model": (
                            config.get("model") if isinstance(config, dict) else None
                        ),
                    },
                    "feedback": feedback_map.get(str(log.log_id)),
                }

                if is_composite_log:
                    children = config.get("children", [])
                    log_item["composite"] = True
                    log_item["aggregate_pass"] = (
                        output_data.get("aggregate_pass")
                        if isinstance(output_data, dict)
                        else None
                    )
                    log_item["detail"]["children"] = children
                    log_item["detail"]["aggregation_function"] = config.get(
                        "aggregation_function"
                    )
                    log_item["detail"]["total_children"] = config.get("total_children")
                    log_item["detail"]["completed_children"] = config.get(
                        "completed_children"
                    )
                    log_item["detail"]["failed_children"] = config.get(
                        "failed_children"
                    )

                log_items.append(log_item)

            response = {
                "template_id": str(template_id),
                "is_composite": template.template_type == "composite",
                "stats": {
                    "total_runs": total_runs,
                    "runs_period": runs_period,
                    "success_count": success_count,
                    "error_count": error_count,
                    "pass_rate": round(
                        (success_count / runs_period * 100) if runs_period > 0 else 0, 2
                    ),
                },
                "chart": chart_data,
                "logs": {
                    "items": log_items,
                    "total": total_logs,
                    "page": page,
                    "page_size": page_size,
                },
            }
            return self._gm.success_response(response)

        except Exception as e:
            logger.error(
                f"Error in EvalUsageStatsView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class EvalFeedbackListView(APIView):
    """
    GET /model-hub/eval-templates/<id>/feedback-list/

    Paginated feedback list with user info.
    Query params: page (0-based), page_size
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import Feedback

        try:
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )

            try:
                EvalTemplate.no_workspace_objects.get(id=template_id, deleted=False)
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            page = int(request.GET.get("page", 0))
            page_size = min(int(request.GET.get("page_size", 25)), 100)

            # Get log IDs for this template as strings (Feedback.source_id is CharField)
            log_ids = list(
                APICallLog.objects.filter(
                    source_id=str(template_id), deleted=False
                ).values_list("log_id", flat=True)[:1000]
            )
            log_id_strs = [str(lid) for lid in log_ids]

            base_qs = (
                Feedback.objects.filter(
                    organization=organization,
                    deleted=False,
                )
                .filter(Q(eval_template_id=template_id) | Q(source_id__in=log_id_strs))
                .select_related("user")
                .order_by("-created_at")
            )

            total = base_qs.count()
            feedbacks = base_qs[page * page_size : (page + 1) * page_size]

            items = []
            for fb in feedbacks:
                user_name = ""
                if fb.user:
                    user_name = getattr(fb.user, "name", "") or fb.user.email

                items.append(
                    {
                        "id": str(fb.id),
                        "value": str(fb.value),
                        "explanation": fb.explanation or "",
                        "source": fb.source or "",
                        "source_id": fb.source_id or "",
                        "action_type": fb.action_type or "",
                        "user_name": user_name,
                        "created_at": (
                            fb.created_at.isoformat() if fb.created_at else ""
                        ),
                    }
                )

            return self._gm.success_response(
                {
                    "template_id": str(template_id),
                    "items": items,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                }
            )

        except Exception as e:
            logger.error(
                f"Error in EvalFeedbackListView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


class TraceEvalView(APIView):
    """
    POST /model-hub/eval-templates/<id>/run-on-trace/

    Run an eval against a trace's data. Extracts input/output from the trace
    and passes it to the eval template.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, template_id, *args, **kwargs):
        from model_hub.types import TraceEvalRequest, TraceEvalResponse
        from model_hub.utils.scoring import determine_pass_fail, normalize_score

        try:
            try:
                req = TraceEvalRequest(**request.data)
            except Exception as e:
                from tfc.utils.errors import format_request_error

                return self._gm.bad_request(format_request_error(e))

            try:
                template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.not_found("Eval template not found.")

            # Get trace data
            from tracer.models.trace import Trace

            try:
                trace = Trace.objects.get(id=req.trace_id, deleted=False)
            except Trace.DoesNotExist:
                return self._gm.not_found("Trace not found.")

            # Extract trace input/output for eval context
            trace_input = trace.input if hasattr(trace, "input") else {}
            trace_output = trace.output if hasattr(trace, "output") else {}

            # Build mapping from trace data
            config = template.config or {}
            required_keys = config.get("required_keys", [])
            mapping = {}

            if req.pass_context:
                # Pass full trace context without explicit mapping
                mapping = {
                    "input": str(trace_input) if trace_input else "",
                    "output": str(trace_output) if trace_output else "",
                    "trace_id": str(trace.id),
                }
            else:
                # Try to map required keys from trace input/output
                for key in required_keys:
                    if isinstance(trace_input, dict) and key in trace_input:
                        mapping[key] = str(trace_input[key])
                    elif isinstance(trace_output, dict) and key in trace_output:
                        mapping[key] = str(trace_output[key])

            # Run eval via existing playground infrastructure
            try:
                from model_hub.views.utils.evals import run_eval_func

                organization = (
                    getattr(request, "organization", None) or request.user.organization
                )
                runtime_config = {"mapping": mapping}

                result = run_eval_func(
                    runtime_config,
                    mapping,
                    template,
                    organization,
                    model=req.model,
                )

                output = result.get("output", {}) if isinstance(result, dict) else {}
                raw_value = output.get("output") if isinstance(output, dict) else result
                output_type = config.get("output", "Pass/Fail")

                score = normalize_score(
                    raw_value,
                    template.output_type_normalized or "pass_fail",
                    choice_scores=template.choice_scores,
                )
                threshold = template.pass_threshold or 0.5
                passed = determine_pass_fail(score, threshold)
                reason = output.get("reason") if isinstance(output, dict) else None

                response = TraceEvalResponse(
                    template_id=str(template_id),
                    trace_id=req.trace_id,
                    score=score,
                    passed=passed,
                    reason=str(reason) if reason else None,
                    status="completed",
                )

            except Exception as eval_error:
                response = TraceEvalResponse(
                    template_id=str(template_id),
                    trace_id=req.trace_id,
                    status="failed",
                    reason=str(eval_error),
                )

            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(f"Error in TraceEvalView: {str(e)}\n{traceback.format_exc()}")
            return self._gm.bad_request(str(e))


class VersionCompareView(APIView):
    """
    GET /model-hub/eval-templates/<id>/versions/compare/?a=1&b=2

    Compare two versions of an eval template.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id, *args, **kwargs):
        from model_hub.models.evals_metric import EvalTemplateVersion
        from model_hub.types import VersionCompareResponse, VersionDiff

        try:
            version_a = request.query_params.get("a")
            version_b = request.query_params.get("b")

            if not version_a or not version_b:
                return self._gm.bad_request(
                    "Query params 'a' and 'b' (version numbers) are required."
                )

            try:
                va = EvalTemplateVersion.objects.get(
                    eval_template_id=template_id, version_number=int(version_a)
                )
                vb = EvalTemplateVersion.objects.get(
                    eval_template_id=template_id, version_number=int(version_b)
                )
            except EvalTemplateVersion.DoesNotExist:
                return self._gm.not_found("One or both versions not found.")

            # Compare fields
            diffs = []
            for field in ["criteria", "model"]:
                val_a = getattr(va, field, "") or ""
                val_b = getattr(vb, field, "") or ""
                diffs.append(
                    VersionDiff(
                        field=field,
                        version_a_value=val_a,
                        version_b_value=val_b,
                        changed=val_a != val_b,
                    )
                )

            # Compare config snapshots
            config_a = str(va.config_snapshot or {})
            config_b = str(vb.config_snapshot or {})
            diffs.append(
                VersionDiff(
                    field="config_snapshot",
                    version_a_value=config_a[:500],
                    version_b_value=config_b[:500],
                    changed=config_a != config_b,
                )
            )

            response = VersionCompareResponse(
                template_id=str(template_id),
                version_a=va.version_number,
                version_b=vb.version_number,
                diffs=[d.model_dump() for d in diffs],
            )
            return self._gm.success_response(response.model_dump())

        except Exception as e:
            logger.error(
                f"Error in VersionCompareView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(str(e))


def _build_span_context(span) -> dict:
    """Build a span_context dict from an ObservationSpan row.

    For voice spans (observation_type == 'conversation' or Vapi-style
    span_attributes present), promotes the most useful nested fields
    (transcript, recording_url, ended_reason, duration, meaningful
    input/output) to the top level so evaluator templates can use:

        {{span.transcript}}
        {{span.recording_url}}
        {{span.ended_reason}}
        {{span.duration_seconds}}
        {{span.input}}   # first user turn
        {{span.output}}  # last assistant turn

    instead of the deeply-nested real locations
    (`{{span.span_attributes.provider_transcript}}` etc.).
    """
    base = {
        "id": span.id,
        "trace_id": str(span.trace_id) if getattr(span, "trace_id", None) else None,
        "name": span.name,
        "observation_type": span.observation_type,
        "input": span.input,
        "output": span.output,
        "span_attributes": span.span_attributes or {},
        "resource_attributes": span.resource_attributes or {},
        "status": span.status,
        "status_message": span.status_message,
        "model": span.model,
        "provider": span.provider,
        "start_time": str(span.start_time) if span.start_time else None,
        "end_time": str(span.end_time) if span.end_time else None,
        "latency_ms": span.latency_ms,
        "cost": float(span.cost) if span.cost is not None else None,
        "prompt_tokens": span.prompt_tokens,
        "completion_tokens": span.completion_tokens,
        "total_tokens": span.total_tokens,
        "metadata": span.metadata or {},
        "tags": span.tags or [],
    }

    sa = span.span_attributes or {}
    is_voice = (
        span.observation_type == "conversation"
        or "vapi.call_id" in sa
        or "provider_transcript" in sa
        or "call_logs" in sa
    )
    if not is_voice:
        return base

    # Voice enrichment — hoist the useful fields.
    base["is_voice"] = True

    # Turn-by-turn transcript. Prefer the clean provider_transcript list
    # (role/content pairs) over the verbose raw_log messages.
    transcript = sa.get("provider_transcript")
    if not isinstance(transcript, list):
        # Fall back to raw_log.messages if present
        raw_log = sa.get("raw_log")
        if isinstance(raw_log, str):
            try:
                raw_log = json.loads(raw_log)
            except Exception:
                raw_log = None
        if isinstance(raw_log, dict):
            msgs = raw_log.get("messages")
            if isinstance(msgs, list):
                # Vapi messages have extra fields (time, secondsFromStart);
                # normalize to {role, content} for template use.
                transcript = [
                    {
                        "role": m.get("role"),
                        "content": m.get("message") or m.get("content"),
                    }
                    for m in msgs
                    if m.get("role") in ("user", "assistant", "bot", "system")
                ]
            else:
                transcript = None

    if isinstance(transcript, list) and transcript:
        base["transcript"] = transcript
        # Derive meaningful input/output from the transcript when the
        # top-level span.input/output are empty (Vapi leaves them null).
        if not base.get("input"):
            _first_user = next(
                (t.get("content") for t in transcript if t.get("role") in ("user",)),
                None,
            )
            if _first_user:
                base["input"] = _first_user
        if not base.get("output"):
            _last_asst = next(
                (
                    t.get("content")
                    for t in reversed(transcript)
                    if t.get("role") in ("assistant", "bot")
                ),
                None,
            )
            if _last_asst:
                base["output"] = _last_asst

    # Recording URLs — look in raw_log first, then flat attributes.
    raw_log = sa.get("raw_log")
    if isinstance(raw_log, str):
        try:
            raw_log = json.loads(raw_log)
        except Exception:
            raw_log = {}
    if not isinstance(raw_log, dict):
        raw_log = {}

    base["recording_url"] = (
        raw_log.get("recordingUrl")
        or raw_log.get("recording_url")
        or sa.get("recording_url")
        or sa.get("recordingUrl")
    )
    base["stereo_recording_url"] = (
        raw_log.get("stereoRecordingUrl")
        or raw_log.get("stereo_recording_url")
        or sa.get("stereo_recording_url")
    )

    # Call-level fields that are commonly referenced in voice evals.
    base["call_status"] = sa.get("call.status") or raw_log.get("status")
    base["duration_seconds"] = (
        sa.get("call.duration")
        or raw_log.get("durationSeconds")
        or raw_log.get("duration_seconds")
    )
    base["ended_reason"] = sa.get("ended_reason") or raw_log.get("endedReason")
    base["provider_call_id"] = sa.get("vapi.call_id") or raw_log.get("id")
    base["provider_summary"] = raw_log.get("summary")

    # Metrics: WPM, interruptions, talk ratio, turn count
    base["metrics"] = {
        "turn_count": sa.get("call.total_turns"),
        "talk_ratio": sa.get("call.talk_ratio"),
        "user_wpm": sa.get("call.user_wpm"),
        "bot_wpm": sa.get("call.bot_wpm"),
        "user_interruptions": sa.get("numUserInterrupted"),
        "ai_interruption_rate": sa.get("ai_interruption_rate"),
        "avg_agent_latency_ms": sa.get("avg_agent_latency_ms"),
        "turn_latency_avg": sa.get("turnLatencyAverage"),
    }

    return base


class EvalPlayGroundAPIView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from tfc.ee_gates import turing_oss_gate_response

        gate = turing_oss_gate_response(request.data.get("model"))
        if gate is not None:
            return gate

        try:
            org = getattr(request, "organization", None) or request.user.organization
            serializer = EvalPlayGroundSerializer(data=request.data)

            if serializer.is_valid():
                validated_data = serializer.validated_data

                model = validated_data.get("model", None)
                kb_id = validated_data.get("kb_id", None)
                error_localizer = validated_data.get("error_localizer", False)
                runtime_config = validated_data.get("config", {}) or {}
                top_level_params = validated_data.get("params", {}) or {}
                mapping = validated_data.get("mapping", {})
                if not mapping and isinstance(runtime_config, dict):
                    mapping = runtime_config.get("mapping", {})
                template_id = validated_data.get("template_id", None)
                input_data_types = validated_data.get("input_data_types", {})
                if not input_data_types and isinstance(runtime_config, dict):
                    input_data_types = runtime_config.get("input_data_types", {})

                # Auto-context payloads. Caller may supply the dicts
                # directly, or IDs that we resolve server-side.
                row_context = validated_data.get("row_context")
                span_context = validated_data.get("span_context")
                trace_context = validated_data.get("trace_context")
                session_context = validated_data.get("session_context")
                call_context = validated_data.get("call_context")
                _span_id = validated_data.get("span_id")
                _trace_id = validated_data.get("trace_id")
                _session_id = validated_data.get("session_id")
                _call_id = validated_data.get("call_id")
                if span_context is None and _span_id:
                    try:
                        from tracer.models.observation_span import ObservationSpan

                        _s = ObservationSpan.objects.filter(id=str(_span_id)).first()
                        if _s:
                            span_context = _build_span_context(_s)
                    except Exception as _e:
                        logger.warning(f"Failed to fetch span {_span_id}: {_e}")
                if trace_context is None and _trace_id:
                    try:
                        from tracer.models.trace import Trace

                        _t = Trace.objects.filter(id=_trace_id).first()
                        if _t:
                            trace_context = {
                                "id": str(_t.id),
                                "project_id": (
                                    str(_t.project_id) if _t.project_id else None
                                ),
                                "name": _t.name,
                                "session_id": (
                                    str(_t.session_id) if _t.session_id else None
                                ),
                                "metadata": _t.metadata or {},
                                "tags": _t.tags or [],
                            }
                    except Exception as _e:
                        logger.warning(f"Failed to fetch trace {_trace_id}: {_e}")
                if session_context is None and _session_id:
                    try:
                        from tracer.models.trace_session import TraceSession

                        _ss = TraceSession.objects.filter(id=_session_id).first()
                        if _ss:
                            session_context = {
                                "id": str(_ss.id),
                                "name": _ss.name,
                                "project_id": (
                                    str(_ss.project_id) if _ss.project_id else None
                                ),
                                "status": _ss.status,
                                "start_time": (
                                    str(_ss.start_time) if _ss.start_time else None
                                ),
                                "end_time": str(_ss.end_time) if _ss.end_time else None,
                            }
                    except Exception as _e:
                        logger.warning(f"Failed to fetch session {_session_id}: {_e}")
                if call_context is None and _call_id:
                    try:
                        from simulate.models.test_execution import (
                            CallExecution,
                            CallTranscript,
                        )

                        _ce = CallExecution.objects.filter(id=_call_id).first()
                        if _ce:
                            call_context = {
                                "id": str(_ce.id),
                                "status": _ce.status,
                                "call_type": _ce.call_type,
                                "simulation_call_type": _ce.simulation_call_type,
                                "phone_number": _ce.phone_number,
                                "started_at": (
                                    str(_ce.started_at) if _ce.started_at else None
                                ),
                                "ended_at": str(_ce.ended_at) if _ce.ended_at else None,
                                "duration_seconds": _ce.duration_seconds,
                                "recording_url": _ce.recording_url,
                                "call_summary": _ce.call_summary,
                                "ended_reason": _ce.ended_reason,
                                "overall_score": (
                                    float(_ce.overall_score)
                                    if _ce.overall_score is not None
                                    else None
                                ),
                                "error_message": _ce.error_message,
                                "message_count": _ce.message_count,
                                "response_time_ms": _ce.response_time_ms,
                                "call_metadata": _ce.call_metadata or {},
                                "analysis_data": _ce.analysis_data or {},
                                "evaluation_data": _ce.evaluation_data or {},
                                "eval_outputs": _ce.eval_outputs or {},
                                "logs_summary": _ce.logs_summary,
                                "scenario": build_eval_playground_scenario_context(_ce),
                                "transcript": [
                                    {
                                        "speaker": t.speaker_role,
                                        "content": t.content,
                                        "start_ms": t.start_time_ms,
                                    }
                                    for t in CallTranscript.objects.filter(
                                        call_execution_id=_ce.id
                                    ).order_by("start_time_ms")[:200]
                                ],
                            }
                    except Exception as _e:
                        logger.warning(f"Failed to fetch call {_call_id}: {_e}")

                if isinstance(runtime_config, dict):
                    config_params = runtime_config.get("params", {})
                    if (
                        not isinstance(config_params, dict) or not config_params
                    ) and isinstance(top_level_params, dict):
                        runtime_config["params"] = top_level_params

                try:
                    eval_template = EvalTemplate.no_workspace_objects.get(
                        id=template_id, deleted=False
                    )
                except EvalTemplate.DoesNotExist:
                    return self._gm.bad_request(
                        get_error_message("MISSING_EVAL_TEMPLATE")
                    )

                try:
                    # Run the evaluation with the provided config
                    response = run_eval_func(
                        runtime_config,
                        mapping,
                        eval_template,
                        org,
                        model=model,
                        error_localizer=error_localizer,
                        source=SourceChoices.EVAL_PLAYGROUND.value,
                        kb_id=kb_id,
                        workspace=request.workspace,
                        input_data_types=input_data_types,
                        row_context=row_context,
                        span_context=span_context,
                        trace_context=trace_context,
                        session_context=session_context,
                        call_context=call_context,
                    )

                    return self._gm.success_response(
                        response if response else "Evaluation has been updated."
                    )
                except Exception as e:
                    if UsageLimitExceeded is not None and isinstance(e, UsageLimitExceeded):
                        logger.warning(f"Eval playground usage limit: {str(e)}")
                        return self._gm.usage_limit_response(e.check_result)
                    logger.error(f"Error in run_eval_func: {str(e)}")
                    return self._gm.bad_request(
                        f"Failed to run Eval due to the reason: {str(e)}"
                    )

            else:
                logger.info(f"serializer error: {serializer.errors}")
                return self._gm.bad_request(serializer.errors)

        except Exception as e:
            logger.exception(f"Error in EvalPlayGroundAPIView: {str(e)}")
            return self._gm.bad_request(f"Error in EvalPlayGroundAPIView: {str(e)}")


class EvalCodeSnippetAPIView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None) or request.user.organization
            model = request.query_params.get("model", None)
            mapping = request.query_params.get("mapping", "")
            template_id = request.query_params.get("template_id", None)
            error_localizer = request.query_params.get("error_localizer", False)

            api_key = OrgApiKey.objects.filter(
                type="user", organization=org, enabled=True, user=request.user
            )
            if not api_key.exists():
                api_key = OrgApiKey.objects.create(
                    organization=org, type="user", enabled=True, user=request.user
                )
            else:
                api_key = api_key.first()
            try:
                mapping = json.loads(mapping) if mapping else {}
            except json.JSONDecodeError:
                mapping = {}
            if not template_id:
                return self._gm.bad_request({"error": "template_id is required"})

            try:
                eval_template = EvalTemplate.no_workspace_objects.get(
                    id=template_id, deleted=False
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.bad_request(get_error_message("MISSING_EVAL_TEMPLATE"))

            if not model:
                model = ModelChoices.TURING_LARGE.value

            code = EVAL_PLAYGROUND_PYTHON_CODE.format(
                api_key.api_key,
                api_key.secret_key,
                eval_template.name,
                mapping,
                f'model_name="{model}"',
            )

            data = {
                "template_id": str(template_id),
                "model": model,
                "mapping": mapping,
                "error_localizer": error_localizer,
            }
            curl_code = EVAL_PLAYGROUND_CURL_CODE.format(
                BASE_URL, api_key.api_key, api_key.secret_key, json.dumps(data)
            )

            js_code = EVAL_PLAYGROUND_JS_CODE.format(
                BASE_URL, api_key.api_key, api_key.secret_key, json.dumps(data)
            )

            return self._gm.success_response(
                {"python": code, "curl": curl_code, "javascript": js_code}
            )

        except Exception as e:
            logger.exception(f"Error in getting code snippet for eval: {str(e)}")
            return self._gm.bad_request(
                f"Error in getting code snippet for eval: {str(e)}"
            )


class EvalPlayGroundFeedbackAPIView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            serializer = EvalPlayGroundFeedbackSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            log_id = validated_data.get("log_id", None)
            action_type = validated_data.get("action_type", None)
            value = validated_data.get("value", None)
            explanation = validated_data.get("explanation", None)

            try:
                log = APICallLog.objects.get(log_id=log_id)
                config = json.loads(log.config)
                required_keys = config.get("required_keys", [])
                input_data_types = config.get("input_data_types", {})
                if not required_keys or len(required_keys) == 0:
                    values = config.get("mappings", {})
                    keys = list(values.keys()) if values else []

                    if len(keys) > 0:
                        required_keys = keys

                values = config.get("mappings", {}).copy()
                if "required_keys" in values:
                    required_keys = values.get("required_keys", [])

                row_dict = config.get("mappings", {})
            except APICallLog.DoesNotExist:
                return self._gm.bad_request("Invalid Evaluation Id provided")

            try:
                feedback = Feedback.objects.get(
                    source_id=log_id,
                    source=SourceChoices.EVAL_PLAYGROUND.value,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
                feedback.value = value
                if explanation:
                    feedback.explanation = explanation
                if action_type:
                    feedback.action_type = action_type
                feedback.save(update_fields=["value", "explanation", "action_type"])
                # print(f"[FEEDBACK] Updated existing feedback id={feedback.id} source_id={log_id} value='{value}' explanation='{explanation}' action_type='{action_type}'", flush=True)

            except Feedback.DoesNotExist:
                # Link feedback to the eval template via the log's source_id
                eval_template = None
                try:
                    eval_template = EvalTemplate.no_workspace_objects.get(
                        id=log.source_id, deleted=False
                    )
                except Exception:
                    pass

                feedback = Feedback.objects.create(
                    source=SourceChoices.EVAL_PLAYGROUND.value,
                    source_id=log_id,
                    eval_template=eval_template,
                    user=request.user,
                    value=value,
                    explanation=explanation,
                    action_type=action_type,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    workspace=None,
                )
                print(
                    f"[FEEDBACK] Created new feedback id={feedback.id} source_id={log_id} eval_template={eval_template.id if eval_template else None} value='{value}' explanation='{explanation}' action_type='{action_type}'",
                    flush=True,
                )

            row_dict["feedback_comment"] = explanation
            row_dict["feedback_value"] = value

            org_for_embedding = str(
                (getattr(request, "organization", None) or request.user.organization).id
            )
            # print(f"[FEEDBACK] Storing embedding for eval_id={log.source_id} org_id={org_for_embedding} required_keys={required_keys} row_dict_keys={list(row_dict.keys())} feedback_value='{value}' feedback_comment='{explanation}'", flush=True)
            embedding_manager = EmbeddingManager()
            try:
                result = embedding_manager.data_formatter(
                    eval_id=str(log.source_id),
                    row_dict=row_dict,
                    inputs_formater=required_keys,
                    insert=True,
                    organization_id=org_for_embedding,
                    workspace_id=None,
                )
                # print(f"[FEEDBACK] data_formatter returned vectors={len(result[0]) if result and result[0] else 0} metadata={len(result[1]) if result and len(result) > 1 else 0}", flush=True)
            except Exception as e:
                # print(f"[FEEDBACK] data_formatter FAILED: {e}", flush=True)
                import traceback

                traceback.print_exc()
            finally:
                embedding_manager.close()

            if action_type == "retune":
                message = "Metric queued for retuning"

            elif action_type == "recalculate":
                message = "Metric queued for recalculation"
                # All args must be JSON-serializable for Temporal.
                # Round-trip through json to strip any Django/Python types
                # (UUID, Decimal, model instances, etc.).
                safe_values = json.loads(json.dumps(values, default=str))
                safe_input_data_types = json.loads(
                    json.dumps(input_data_types, default=str)
                )
                run_eval_func_task.delay(
                    safe_values,
                    str(log.source_id),
                    str(
                        (
                            getattr(request, "organization", None)
                            or request.user.organization
                        ).id
                    ),
                    config.get("model", None),
                    config.get("kb_id", None),
                    str(log_id),
                    str(request.workspace.id) if request.workspace else None,
                    input_data_types=safe_input_data_types,
                )
            else:
                pass

            return self._gm.success_response(
                {"message": message, "feedback_id": str(feedback.id)}
            )

        except Exception as e:
            logger.exception(f"Error in Feedback eval playground API: {str(e)}")
            return self._gm.bad_request(
                f"Error in Feedback eval playground API: {str(e)}"
            )


class UpdateEvalTemplateView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None) or request.user.organization

            serializer = UpdateEvalTemplateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            name = validated_data.get("name", None)
            function_eval = validated_data.get("function_eval", None)
            description = validated_data.get("description", None)
            criteria = validated_data.get("criteria", None)
            eval_tags = validated_data.get("eval_tags", [])
            multi_choice = validated_data.get("multi_choice", False)
            choices_map = validated_data.get("choices_map", {})
            model = validated_data.get("model", None)
            eval_template_id = validated_data.get("eval_template_id", None)
            check_internet = validated_data.get("check_internet", False)
            required_keys = validated_data.get("required_keys", [])

            try:
                eval_template = EvalTemplate.objects.get(
                    id=eval_template_id,
                    organization=org,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.bad_request(get_error_message("MISSING_EVAL_TEMPLATE"))

            config = eval_template.config
            eval_template.description = (
                description if description else eval_template.description
            )
            eval_template.criteria = criteria if criteria else eval_template.criteria
            eval_template.eval_tags = (
                eval_tags if eval_tags else eval_template.eval_tags
            )
            eval_template.multi_choice = (
                multi_choice if multi_choice else eval_template.multi_choice
            )

            if name is not None:
                if EvalTemplate.objects.filter(
                    name=name,
                    organization=org,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                ).exists():
                    raise Exception(get_error_message("EVAL_TEMPLATE_ALREADY_EXISTS"))
                else:
                    eval_template.name = name

            if model is not None:
                config["model"] = model
                eval_template.model = model

            if choices_map is not None and len(list(choices_map.keys())) > 0:
                config["choices_map"] = choices_map
                eval_template.choices = list(choices_map.keys())

            if check_internet is not None:
                config["check_internet"] = check_internet

            if required_keys is not None and len(required_keys) > 0:
                config["required_keys"] = required_keys

            if function_eval:
                configuration = eval_template.config.copy()
                configuration["function_eval"] = True
                configuration["config"] = validated_data.get("config", {}).get("config")
                config = configuration

            eval_template.config = config
            eval_template.updated_at = timezone.now()
            eval_template.save(
                update_fields=[
                    "description",
                    "criteria",
                    "eval_tags",
                    "multi_choice",
                    "model",
                    "choices",
                    "config",
                    "updated_at",
                    "name",
                ]
            )

            return self._gm.success_response("Evaluation template updated successfully")

        except Exception as e:
            logger.exception(f"Error updating the eval template: {str(e)}")
            return self._gm.bad_request(f"error updating the eval template {str(e)}")


class DeleteEvalTemplateView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None) or request.user.organization

            serializer = DeleteEvalTemplateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            eval_template_id = validated_data.get("eval_template_id", None)

            try:
                eval_template = EvalTemplate.objects.get(
                    id=eval_template_id,
                    organization=org,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist as e:
                raise Exception(get_error_message("MISSING_EVAL_TEMPLATE")) from e

            # Use transaction to ensure all operations are atomic
            with transaction.atomic():
                eval_template.deleted = True
                eval_template.deleted_at = timezone.now()
                eval_template.save(update_fields=["deleted", "deleted_at"])

                # Delete all related objects that reference this EvalTemplate

                UserEvalMetric.objects.filter(template=eval_template).update(
                    deleted=True, deleted_at=timezone.now()
                )
                PromptEvalConfig.objects.filter(eval_template=eval_template).update(
                    deleted=True, deleted_at=timezone.now()
                )
                CustomEvalConfig.objects.filter(eval_template=eval_template).update(
                    deleted=True, deleted_at=timezone.now()
                )
                InlineEval.objects.filter(
                    evaluation__eval_template=eval_template
                ).update(deleted=True, deleted_at=timezone.now())
                ExternalEvalConfig.objects.filter(eval_template=eval_template).update(
                    deleted=True, deleted_at=timezone.now()
                )
                APICallLog.objects.filter(source_id=eval_template_id).update(
                    deleted=True, deleted_at=timezone.now()
                )
                EvalLogger.objects.filter(
                    custom_eval_config__eval_template=eval_template
                ).update(deleted=True, deleted_at=timezone.now())

            return self._gm.success_response("Evaluation template Deleted successfully")

        except Exception as e:
            logger.exception(f"Error updating the eval template: {str(e)}")
            return self._gm.bad_request(f"error updating the eval template {str(e)}")


class DuplicateEvalTemplateView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None) or request.user.organization

            serializer = DuplicateEvalTemplateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data
            eval_template_id = validated_data.get("eval_template_id", None)
            name = validated_data.get("name", None)

            try:
                eval_template = EvalTemplate.objects.get(
                    id=eval_template_id,
                    organization=org,
                    owner=OwnerChoices.USER.value,
                    deleted=False,
                )
            except EvalTemplate.DoesNotExist as e:
                raise Exception(get_error_message("MISSING_EVAL_TEMPLATE")) from e

            if EvalTemplate.objects.filter(
                name=name,
                organization=org,
                owner=OwnerChoices.USER.value,
                deleted=False,
            ).exists():
                raise Exception(get_error_message("EVAL_TEMPLATE_ALREADY_EXISTS"))

            fields_to_copy = {
                field.name: getattr(eval_template, field.name)
                for field in eval_template._meta.fields
                if field.name not in ["id", "created_at", "updated_at", "name"]
            }
            fields_to_copy["name"] = name
            fields_to_copy["organization"] = org  # Explicitly set organization
            fields_to_copy["created_at"] = timezone.now()
            fields_to_copy["updated_at"] = timezone.now()

            # Create the new EvalTemplate instance
            new_eval_template = EvalTemplate.objects.create(**fields_to_copy)

            return self._gm.success_response(
                {
                    "message": "Evaluation template duplicated successfully",
                    "eval_template_id": str(new_eval_template.id),
                }
            )

        except Exception as e:
            logger.exception(f"Error duplicating the eval template: {str(e)}")
            return self._gm.bad_request(f"error duplicating the eval template {str(e)}")


class TestEvaluationTemplateAPIView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from tfc.ee_gates import turing_oss_gate_response

        gate = turing_oss_gate_response(request.data.get("model"))
        if gate is not None:
            return gate

        try:
            serializer = TestEvalTemplateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            template_type = validated_data.get("template_type", None)
            mappings = validated_data["config"].get("mapping", {})
            input_data_types = validated_data.get("input_data_types", {})
            model = validated_data.get("model", None)
            config = validated_data.get("config", {})

            try:
                eval_template = EvalTemplate.no_workspace_objects.get(
                    name="deterministic_evals"
                )
            except EvalTemplate.DoesNotExist:
                return self._gm.bad_request(get_error_message("MISSING_EVAL_TEMPLATE"))

            if not template_type:
                return self._gm.bad_request(get_error_message("MISSING_TEMPLATE_TYPE"))

            config = prepare_user_eval_config(validated_data, True)
            if template_type == EvalTemplateType.FUTUREAGI.value:
                eval_id = "DeterministicEvaluator"

            elif template_type == EvalTemplateType.LLM.value:
                eval_id = "CustomPromptEvaluator"
                data_config = config.get("config", {})
                data_config["organization_id"] = str(
                    (
                        getattr(request, "organization", None)
                        or request.user.organization
                    ).id
                )
                config["config"] = data_config

            elif template_type == EvalTemplateType.FUNCTION.value:
                eval_id = validated_data.get("eval_type_id")
                if not eval_id:
                    return self._gm.bad_request(
                        "eval_type_id is required for Function evaluations"
                    )

                function_template = EvalTemplate.no_workspace_objects.filter(
                    config__eval_type_id=eval_id,
                    deleted=False,
                ).filter(
                    Q(organization=request.user.organization)
                    | Q(organization__isnull=True)
                )

                function_template = function_template.order_by("-updated_at").first()

                if function_template and has_function_params_schema(
                    function_template.config
                ):
                    config = normalize_eval_runtime_config(
                        function_template.config, config
                    )
                else:
                    outer_config = config.get("config", {})
                    func_config = outer_config.get("config", {})

                    for key, value in func_config.items():
                        if (
                            isinstance(value, list)
                            and value
                            and isinstance(value[0], dict)
                            and "value" in value[0]
                        ):
                            func_config[key] = [item.get("value") for item in value]

                    config["config"] = func_config
                # Function evals use Pass/Fail output type
                config["output"] = EvalOutputType.PASS_FAIL.value

            else:
                return self._gm.bad_request(
                    f"Unsupported template_type: {template_type}"
                )

            # Run the evaluation with the provided config
            response = run_eval_func(
                config,
                mappings,
                eval_template,
                getattr(request, "organization", None) or request.user.organization,
                input_data_types=input_data_types,
                type="user_built",
                model=model,
                eval_id=eval_id,
                error_localizer=validated_data.get("error_localizer", False),
                test=True,
                source="eval_playground_test",
                workspace=request.workspace,
            )

            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in TestEvaluationTemplateAPIView: {str(e)}")
            return self._gm.bad_request(str(e))


def get_display_value(value):
    """
    Convert a given value to a displayable string format for cell rendering.
    """
    if isinstance(value, str):
        return value
    elif isinstance(value, list):
        result = ""
        for item in value:
            if item and not isinstance(item, str):
                item = str(item)
            result += item + "\n"
        return result
    elif isinstance(value, dict):
        return json.dumps(value)
    return ""


def get_column_data(eval_template_id, source, user):
    try:
        with transaction.atomic():
            try:
                setting, created = EvalSettings.objects.get_or_create(
                    eval_id=eval_template_id, source=source, deleted=False, user=user
                )
            except IntegrityError:
                setting = EvalSettings.objects.get(
                    eval_id=eval_template_id, source=source, deleted=False, user=user
                )

            column_data = setting.column_config if setting else []

            if not column_data or len(column_data) == 0:
                column_data = create_column_config_playground(eval_template_id, source)

            setting.column_config = column_data
            setting.save(update_fields=["column_config"])

            return column_data

    except Exception as e:
        logger.exception(f"Error in get_column_data: {str(e)}")
        return []


def batch_queryset(queryset: QuerySet, batch_size: int):
    start = 0
    total = queryset.count()
    while start < total:
        yield queryset[start : start + batch_size]
        start += batch_size


def populate_log_row_data(eval_template, logs, key_map):
    try:
        row_data = []
        for log in logs:
            config = json.loads(log.config)
            row_id = str(uuid.uuid4())
            column_config = {
                "row_id": row_id,
            }

            input_data = config.get("mappings", {})
            output = config.get("output", None)

            for col_key, key in key_map.items():
                value = ""
                status = ""

                if key in input_data:
                    value = get_display_value(input_data[key])
                    status = "success"
                elif key in config:
                    value = config[key]
                    status = "success"
                else:
                    match key:
                        case eval_template.name:
                            value = output
                            status = log.status
                        case "Criteria":
                            value = eval_template.criteria
                        case "Tags":
                            value = eval_template.eval_tags
                        case "Created At":
                            value = log.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        case "Updated At":
                            value = log.updated_at.strftime("%Y-%m-%d %H:%M:%S")
                        case "Evaluation ID":
                            value = log.log_id
                        case "Source":
                            value = (
                                config.get("source").replace("_", " ").title()
                                if config.get("source")
                                else (
                                    log.source.replace("_", " ").title()
                                    if log.source
                                    else "Unknown"
                                )
                            )
                        case "Evaluation Feedback":
                            if log.log_id:
                                try:
                                    feedback = Feedback.objects.get(
                                        source_id=log.log_id,
                                        source=SourceChoices.EVAL_PLAYGROUND.value,
                                        organization=log.organization,
                                    )
                                    value = feedback.value
                                except Feedback.DoesNotExist:
                                    value = ""
                            else:
                                value = ""
                        case "Feedback Explanation":
                            if log.log_id:
                                try:
                                    feedback = Feedback.objects.get(
                                        source_id=log.log_id,
                                        source=SourceChoices.EVAL_PLAYGROUND.value,
                                        organization=log.organization,
                                    )
                                    value = feedback.explanation
                                except Feedback.DoesNotExist:
                                    value = ""
                            else:
                                value = ""
                        case _:
                            value = ""
                column_config[col_key] = {
                    "cell_value": value,
                    "status": status or "success",
                    "search_results": {},
                }

            column_config["log_id"] = log.log_id
            column_config["input_data_types"] = config.get("input_data_types", {})

            row_data.append(column_config)

        return row_data
    except Exception as e:
        logger.exception(f"Error in populate_log_row_data: {str(e)}")
        raise


def apply_search(row_data, search_query, column_data):
    search_key = search_query.get("key", "")
    search_value = search_query.get("type", ["text", "image", "audio"])

    if not search_key:
        return row_data

    matched_log_ids = set()

    config_map = [col.get("id") for col in column_data if col.get("is_visible", False)]
    if "text" in search_value:
        for item in row_data:
            log_id = item["log_id"] or None
            for key, value in item.items():
                if key not in config_map:
                    continue
                start_index = -1
                if isinstance(value, dict):
                    start_index = (
                        str(value.get("cell_value", "")).lower().find(search_key)
                    )
                else:
                    start_index = str(value).lower().find(search_key)

                if start_index != -1:
                    matched_log_ids.add(log_id)
                    end_index = start_index + len(search_key)
                    item[key].update(
                        {
                            "key_exists": True,
                            "start_index": start_index,
                            "end_index": end_index,
                        }
                    )

    filtered_rows = []
    for row in row_data:
        if row.get("log_id") in matched_log_ids:
            row["key_exists"] = True
            filtered_rows.append(row)

    return filtered_rows


def create_column_config_playground(eval_template_id, source):
    default_config = {
        "is_frozen": None,
        "is_visible": True,
        "status": "completed",
        "source_type": "text",
    }
    data_type = {
        "score": "float",
        "numeric": "float",
        "choices": "text",
        "Pass/Fail": "boolean",
        "reason": "text",
        "datetime": "datetime",
    }
    eval_template = get_object_or_404(EvalTemplate, id=eval_template_id)
    eval_config = eval_template.config
    output_type = eval_config.get("output", None)
    if not output_type:
        raise Exception("Output Type missing.")
    column_keys = eval_config.get("required_keys", [])
    column_data = []
    column_index = 1

    def add_special_column(name, extra_fields=None):
        nonlocal column_index
        col = {
            "id": f"column{column_index}",
            "name": name,
            **default_config,
        }
        if extra_fields:
            col.update(extra_fields)
        column_data.append(col)
        column_index += 1

    add_special_column("Evaluation ID")

    for key in column_keys:
        column_data.append(
            {
                "id": f"column{column_index}",
                "name": key,
                "data_type": "text",
                **default_config,
            }
        )
        column_index += 1

    add_special_column(
        eval_template.name,
        {
            "origin_type": SourceChoices.EVALUATION.value,
            "data_type": data_type[output_type],
            "output_type": output_type,
        },
    )
    if eval_template.criteria:
        add_special_column("Criteria", {"is_visible": False})

    add_special_column("Created At", {"is_visible": False, "data_type": "datetime"})
    add_special_column("Source", {"is_visible": False})

    if source == "feedback":
        add_special_column("Evaluation Feedback")
        add_special_column("Feedback Explanation")
    elif source == "logs":
        add_special_column("Evaluation Feedback", {"is_visible": False})
        add_special_column("Feedback Explanation", {"is_visible": False})

    return column_data
