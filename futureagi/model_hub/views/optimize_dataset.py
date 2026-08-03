# views.py

import time

import structlog
from django.db.models import Case, Prefetch, When
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import NotFound
from rest_framework.generics import CreateAPIView, ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import PromptOptimizer
except ImportError:
    PromptOptimizer = _ee_stub("PromptOptimizer")
from model_hub.models import OptimizeDataset
from model_hub.models.ai_model import AIModel
from model_hub.models.column_config import ColumnConfig
from model_hub.models.conversations import Message, Node
from model_hub.models.metric import Metric
from model_hub.serializers.column_config import ColumnConfigSerializer
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    ModelHubEmptyRequestSerializer,
    OptimizeDatasetColumnConfigResponseSerializer,
    OptimizeDatasetColumnConfigUpdateRequestSerializer,
    OptimizeDatasetColumnConfigUpdateResponseSerializer,
    OptimizeDatasetCreateResponseSerializer,
    OptimizeDatasetDetailResponseSerializer,
    OptimizeDatasetKnowledgeBaseCreateResponseSerializer,
    OptimizeDatasetKnowledgeBaseDetailResponseSerializer,
    OptimizeDatasetKnowledgeBaseListResponseSerializer,
    OptimizeDatasetKnowledgeBaseRequestSerializer,
    OptimizeDatasetListQuerySerializer,
    OptimizeDatasetMutationRequestSerializer,
    OptimizeDatasetPageRequestSerializer,
    OptimizeDatasetPaginatedResponseSerializer,
    OptimizeDatasetTemplateResultsResponseSerializer,
)
from model_hub.serializers.optimize_dataset import (
    OptimizeDatasetKbSerializer,
    OptimizeDatasetSerializer,
)
from model_hub.tasks.agent import create_criteria_text_prompt
from model_hub.tasks.prompt_template_optimizer import get_topk_prompts
from model_hub.utils.constant import optimize_table_columns
from model_hub.utils.optimize import (
    get_prompt_template_columns,
    get_right_answer_columns,
)
from model_hub.utils.utils import check_valid_metrics
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace,
    scoped_ai_model_queryset,
    scoped_column_config_for_identifier,
    scoped_optimize_dataset_queryset,
)
from tfc.temporal import temporal_activity
from tfc.utils.api_contracts import validated_request
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination
from tfc.utils.types import Environments

logger = structlog.get_logger(__name__)


def _clickhouse_environment_value(environment):
    return Environments.convert_to_type(environment) or environment


OPTIMIZE_DATASET_DYNAMIC_ROW_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "id": openapi.Schema(type=openapi.TYPE_STRING),
        "input": openapi.Schema(type=openapi.TYPE_STRING),
        "output": openapi.Schema(type=openapi.TYPE_STRING),
        "right_answer": openapi.Schema(type=openapi.TYPE_STRING),
    },
    additional_properties=openapi.Schema(
        type=openapi.TYPE_NUMBER,
        description="Metric score column keyed by metric id and score variant.",
    ),
)
OPTIMIZE_DATASET_DYNAMIC_ROWS_RESPONSE_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "results": openapi.Schema(
            type=openapi.TYPE_ARRAY,
            items=OPTIMIZE_DATASET_DYNAMIC_ROW_SCHEMA,
        ),
        "count": openapi.Schema(type=openapi.TYPE_INTEGER),
        "total_pages": openapi.Schema(type=openapi.TYPE_INTEGER),
        "current_page": openapi.Schema(type=openapi.TYPE_INTEGER),
        "next": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "previous": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "message": openapi.Schema(type=openapi.TYPE_STRING),
    },
    required=[
        "results",
        "count",
        "total_pages",
        "current_page",
        "next",
        "previous",
        "message",
    ],
)


def _validate_request(serializer_class, data):
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        return None, Response(serializer.errors, status=400)
    return serializer.validated_data, None


def _execute_clickhouse_read_or_empty(clickhouse_client, query, *, operation):
    try:
        return clickhouse_client.execute(query)
    except Exception as exc:
        logger.warning(
            "legacy_optimize_dataset_clickhouse_read_failed",
            operation=operation,
            error=str(exc),
        )
        return []


class OptimizedDatasetView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=OptimizeDatasetListQuerySerializer,
        responses={
            200: OptimizeDatasetPaginatedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def get(self, request, model_id, *args, **kwarg):
        try:
            filters = request.validated_query_data["filters"]

            model = get_object_or_404(
                scoped_ai_model_queryset(request).only("id"),
                id=model_id,
            )

            filter_dict = {"model": model}
            for filter_item in filters:
                filter_key = filter_item["key"]
                filter_value = filter_item["value"]
                if filter_item["operator"] == "between":
                    filter_dict[f"{filter_key}__gte"] = filter_value[0]
                    filter_dict[f"{filter_key}__lte"] = filter_value[1]
                else:
                    filter_dict[filter_key] = filter_value[0]

            optimized_dataset_queryset = scoped_optimize_dataset_queryset(
                request
            ).filter(**filter_dict)

            paginator = ExtendedPageNumberPagination()
            paginator.page_size = 15
            result_page = paginator.paginate_queryset(
                optimized_dataset_queryset, request
            )

            serializer = OptimizeDatasetSerializer(result_page, many=True)
            return paginator.get_paginated_response(serializer.data)

        except (AIModel.DoesNotExist, Http404):
            raise NotFound("AI model not found.") from None
        except NotFound:
            raise
        except Exception as e:
            logger.error(e)
            return GeneralMethods().internal_server_error_response(str(e))

    @validated_request(
        request_serializer=OptimizeDatasetMutationRequestSerializer,
        responses={
            200: OptimizeDatasetCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, model_id, *args, **kwargs):
        try:
            data = request.validated_data
            name = data.get("name")
            start_date = data.get("start_date").split("T")[0]
            end_date = data.get("end_date").split("T")[0]
            payload_model_id = data.get("model")
            optimize_type = data.get("optimize_type")
            environment = data.get("environment")
            version = data.get("version")
            metrics = data.get("metrics")
            prompt = data.get("prompt")
            variables = data.get("variables")

            if str(payload_model_id) != str(model_id):
                return self._gm.bad_request("Payload model must match the URL model.")

            model = get_object_or_404(
                scoped_ai_model_queryset(request).only("id"),
                id=model_id,
            )

            if optimize_type == OptimizeDataset.OptimizeType.TEMPLATE:
                pass
            elif optimize_type == OptimizeDataset.OptimizeType.RAG_TEMPLATE:
                pass
            else:
                pass

            valid, msg = check_valid_metrics(
                (
                    "EVALUATE_PROMPT_TEMPLATE"
                    if optimize_type == OptimizeDataset.OptimizeType.TEMPLATE
                    else "EVALUATE_ANSWER"
                ),
                str(model.id),
            )

            if not valid:
                return self._gm.bad_request(get_error_message("IN_VALID_METRICS"))

            metrics_list = Metric.objects.filter(model=model, id__in=metrics)
            if metrics_list.count() != len(set(metrics)):
                return self._gm.bad_request(
                    "Metrics are not accessible for this model."
                )

            optim_dict = {
                "name": name,
                "optimize_type": optimize_type,
                "start_date": start_date,
                "end_date": end_date,
                "model": model,
                "environment": environment,
                "version": version,
                "prompt": prompt,
                "variables": variables,
                "status": OptimizeDataset.StatusType.RUNNING.value,
                "organization": request_organization(request),
                "workspace": request_workspace(request),
            }

            optim_obj = OptimizeDataset(**optim_dict)
            optim_obj.save()
            optim_obj.metrics.set(metrics_list)
            optim_obj_id = optim_obj.id

            if optim_obj.optimize_type == OptimizeDataset.OptimizeType.TEMPLATE:
                get_topk_prompts.apply_async(
                    args=(
                        str(optim_dict["model"].id),
                        optim_dict["environment"],
                        optim_dict["version"],
                        metrics,
                        optim_obj_id,
                        start_date,
                        end_date,
                    )
                )
            elif optim_obj.optimize_type == OptimizeDataset.OptimizeType.RAG_TEMPLATE:
                optimize_dataset = optim_obj
                if len(optimize_dataset.criteria_breakdown) != 0:
                    criteria_breakdown = optimize_dataset.criteria_breakdown
                else:
                    metrics_list = list(optimize_dataset.metrics.all())
                    input_eval_prompts = [
                        str(metric.text_prompt) for metric in metrics_list
                    ]
                    metric = "\n".join(
                        [
                            f"{i}. {criteria}"
                            for i, criteria in enumerate(input_eval_prompts)
                        ]
                    )
                    criteria_breakdown = create_criteria_text_prompt(metric)

                    optimize_dataset.criteria_breakdown = criteria_breakdown
                    optimize_dataset.save()
                prompt_optim_agent = PromptOptimizer(
                    prompt=prompt,
                    train_data="",
                    criteria_breakdown=criteria_breakdown,
                    optimize_rag=True,
                    filter_keys=optim_obj.knowledge_base_filters,
                    variables=variables,
                )
                top_k_optimized_prompts = prompt_optim_agent.get_optimized_prompt()

                optimized_prompts = [
                    instruction_template["instruction_template"]
                    for instruction_template in top_k_optimized_prompts
                ]
                optim_obj.optimized_k_prompts = optimized_prompts
                optim_obj.status = OptimizeDataset.StatusType.COMPLETED
                optim_obj.save()

            return Response(
                {
                    "status": "Success",
                    "message": "Model created successfully",
                    "data": {"id": optim_obj.id},
                },
                status=200,
            )
        except Http404:
            raise NotFound("AI model not found.") from None
        except Exception as e:
            logger.error(e)
            return GeneralMethods().internal_server_error_response(str(e))


class OptimizeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: OptimizeDatasetDetailResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, model_id, optimization_id):
        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        optimized_dataset = get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )

        serializer = OptimizeDatasetSerializer(optimized_dataset)

        return Response(
            {
                "status": "Success",
                "data": serializer.data,
            },
            status=200,
        )


class KOptimizedPromptTemplateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwarg):
        try:
            optimization_id = request.query_params.get("optim_id")
            k_optimized_prompts = OptimizeDataset.objects.filter(
                id=optimization_id
            ).values("optimizedKPrompts")

            return Response(
                {
                    "status": "Success",
                    "message": "Fetched prompts successfully",
                    "data": k_optimized_prompts,
                },
                status=200,
            )

        except Exception as e:
            return GeneralMethods().internal_server_error_response(str(e))


class RightAnswerResultsView(APIView):
    permission_classes = [IsAuthenticated]

    def construct_query(
        self,
        model_id,
        environment,
        version,
        start_date,
        end_date,
        limit=10,
        offset=0,
        filters=None,
        sort_key="createdAt",
        sort_order="desc",
    ):
        # filter_query = self.get_filter_query(filters)

        # sort_query = self.get_order_query(sort_key, sort_order)

        if filters is None:
            filters = []
        query = f"""
            SELECT
                UUID,
                EventDateTime,
                EvalResults.Key,
                EvalResults.Value,
                arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS input_node_id,
                arrayElement(PredictionLabel.Value, indexOf(PredictionLabel.Key, 'node_id')) AS output_node_id
            FROM events
                WHERE AIModel = '{model_id}'
                AND ModelVersion = '{version}'
                AND Environment = {environment}
                AND has(Features.Key,'node_id')
                AND deleted = 0
                AND EventDateTime > toDateTime('{start_date}')
                AND  EventDateTime < toDateTime('{end_date}')
            LIMIT {limit} OFFSET {offset}
        """

        return query

    def get_order_query(
        self,
        sort_key="created_at",
        sort_order="desc",
    ):
        if sort_key in ("created_at", "createdAt"):
            return f" ORDER BY EventDateTime {sort_order} "
        else:
            return f""" ORDER BY
            CASE
                WHEN arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{sort_key}_score')) = '' THEN 0
                WHEN isNaN(toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{sort_key}_score')))) THEN 0
                ELSE toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{sort_key}_score')))
            END
            {sort_order} """

    def get_sql_based_on_operator(self, filter, column_name):
        operator = filter["operator"]
        first_value = filter["value"][0]
        second_value = filter["value"][1] if len(filter["value"]) > 1 else None

        if type(first_value) is str:
            first_value = f" '{first_value}' "

        if type(second_value) is str:
            second_value = f" '{second_value}' "

        if operator == "between":
            return (
                f" {column_name} > {first_value} AND  {column_name} < {second_value} "
            )

        elif operator == "notBetween":
            return (
                f" {column_name} <= {first_value} AND  {column_name} >= {second_value} "
            )

        elif operator == "equal":
            return f" {column_name} = {first_value} "
        elif operator == "notEqual":
            return f" {column_name} != {first_value} "
        elif operator == "greaterThan":
            return f" {column_name} > {first_value} "
        elif operator == "greaterThanEqualTo":
            return f" {column_name} >= {first_value} "
        elif operator == "lessThan":
            return f" {column_name} < {first_value} "
        elif operator == "lessThanEqualTo":
            return f" {column_name} <= {first_value} "

    def get_filter_query(self, filters=None):
        if filters is None:
            filters = []
        filter_arr = []

        metric_filters = [f for f in filters if f["key"] == "metric"]
        score_filters = [f for f in filters if f["key"] == "score"]
        create_at_filters = [
            f for f in filters if f["key"] in ("created_at", "createdAt")
        ]

        if len(metric_filters) > 0 and len(score_filters) > 0:
            # Apply these filter if there exists a score and metric
            for metric in metric_filters:
                for score in score_filters:
                    filter_arr.append(
                        self.get_sql_based_on_operator(
                            score,
                            f""" CASE
                                WHEN arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric["value"][0]}_score')) = '' THEN 0
                                WHEN isNaN(toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric["value"][0]}_score')))) THEN 0
                                ELSE toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric["value"][0]}_score')))
                            END """,
                        )
                    )

        for create_at_filter in create_at_filters:
            filter_arr.append(
                self.get_sql_based_on_operator(create_at_filter, "EventDate")
            )

        filter_query = ""

        if len(filter_arr) > 0:
            filter_query = " AND " + " AND ".join(filter_arr)

        return filter_query

    def construct_count_query(
        self, model_id, environment, version, start_date, end_date, filters=None
    ):
        # filter_query = self.get_filter_query(filters)

        if filters is None:
            filters = []
        query = f"""
            SELECT
                COUNT(*)
            FROM events
                WHERE AIModel = '{model_id}'
                AND ModelVersion = '{version}'
                AND Environment = {environment}
                AND has(Features.Key,'node_id')
                AND EventDateTime > toDateTime('{start_date}')
                AND  EventDateTime < toDateTime('{end_date}')
                AND deleted = 0
        """

        return query

    def extract_content_from_msg(self, content):
        result = []
        if content["content_type"] == "text":
            result = content["parts"][0]
        else:
            for model_input in content["parts"]:
                msg = {}

                if "image_url" in model_input["type"]:
                    msg["url"] = model_input.get("image_url", {}).get("url", "")

                if "text" in model_input["type"]:
                    msg["text"] = model_input.get("text", "")

                result.append(msg)

        return result

    @validated_request(
        request_serializer=OptimizeDatasetPageRequestSerializer,
        responses={
            200: OPTIMIZE_DATASET_DYNAMIC_ROWS_RESPONSE_SCHEMA,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, model_id, optimization_id):
        payload = request.validated_data

        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        optimization = get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )

        page = payload["page"]
        limit = payload["limit"]
        offset = (int(page) - 1) * limit
        environment = _clickhouse_environment_value(optimization.environment)
        version = optimization.version
        start_date = optimization.start_date.strftime("%Y-%m-%d %H:%M:%S")
        end_date = optimization.end_date.strftime("%Y-%m-%d %H:%M:%S")

        # filters = request.data["filters"] or []
        # sort_order = request.data["sort_order"]
        # sort_key = request.data["sort_key"]

        clickhouse_client = ClickHouseClientSingleton()
        raw_data_points = _execute_clickhouse_read_or_empty(
            clickhouse_client,
            self.construct_query(
                model_id,
                environment,
                version,
                start_date,
                end_date,
                limit,
                offset,
            ),
            operation="right_answers_rows",
        )

        data_points_count = _execute_clickhouse_read_or_empty(
            clickhouse_client,
            self.construct_count_query(
                model_id, environment, version, start_date, end_date
            ),
            operation="right_answers_count",
        )

        total_count = int(data_points_count[0][0]) if data_points_count else 0

        total_pages = (total_count + limit - 1) // limit

        model_input_ids = []
        model_output_ids = []

        for p in raw_data_points:
            model_input_ids.append(p[4])
            model_output_ids.append(p[5])

        order = Case(
            *[When(id=val, then=pos) for pos, val in enumerate(model_input_ids)]
        )
        node_message_input = (
            Node.objects.filter(id__in=model_input_ids)
            .prefetch_related(
                Prefetch("message", queryset=Message.objects.only("content"))
            )
            .order_by(order)
        )

        order = Case(
            *[When(id=val, then=pos) for pos, val in enumerate(model_output_ids)]
        )
        node_message_output = (
            Node.objects.filter(id__in=model_output_ids)
            .prefetch_related(
                Prefetch("message", queryset=Message.objects.only("content"))
            )
            .order_by(order)
        )

        # metrics = get_metric_columns(model_id, dataset)

        formatted_data_points = []
        idx_node = 0

        # print("BLA", model_input_ids)

        for idx in range(len(raw_data_points)):
            each_data_point = raw_data_points[idx]
            obj = {
                "id": each_data_point[0],
                "input": node_message_input[idx_node].message.content["parts"][0],
                "output": node_message_output[idx_node].message.content["parts"][0],
                # "right_answer"
            }

            try:
                right_ans_idx = each_data_point[2].index(
                    f"optimized_right_ans_{optimization_id}_final_answer"
                )
                obj["right_answer"] = each_data_point[3][right_ans_idx]
            except Exception:
                # print((e, 111))
                obj["right_answer"] = None

            # obj["input"] = self.extract_content_from_msg(
            #     node_message_input[idx_node].message.content
            # )

            # obj["past_input"] = Node.objects.get_all_parent_messages(
            #     model_input_ids[idx], self.extract_content_from_msg
            # )[:-1]

            # obj["output"] = self.extract_content_from_msg(
            #     node_message_output[idx_node].message.content
            # )

            for metric in optimization.metrics.all():
                try:
                    # old_idx = each_data_point[2].index(
                    #     f"optimized_right_ans_{optimization_id}_{metric.id}_original_score"
                    # )
                    old_idx = each_data_point[2].index(f"metric_{metric.id}_score")
                    old_score = float(each_data_point[3][old_idx])
                except Exception:
                    # print((e, each_data_point[2]), 345)
                    old_score = None

                obj[f"{str(metric.id)}-old"] = old_score

                try:
                    new_idx = each_data_point[2].index(
                        f"optimized_right_ans_{optimization_id}_{metric.id}_final_score"
                    )
                    new_score = float(each_data_point[3][new_idx])
                except Exception:
                    # print((e, each_data_point[2]), 123)
                    new_score = None

                obj[f"{str(metric.id)}-new"] = new_score

            formatted_data_points.append(obj)

            idx_node += 1

        return Response(
            {
                "results": formatted_data_points,
                "count": total_count,
                "total_pages": total_pages,
                "current_page": page,
                "next": page < total_pages,
                "previous": page > 1,
                "message": "success",
            }
        )


class TemplateResultsView(APIView):
    permission_classes = [IsAuthenticated]

    def construct_query(
        self,
        model_id,
        optimization_id,
        environment,
        version,
        metric_id,
    ):
        query = f"""
            WITH
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_0_score')) AS temp1_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_1_score')) AS temp2_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_2_score')) AS temp3_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_3_score')) AS temp4_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_4_score')) AS temp5_score,
                arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_original')) AS original_score,
                if(isNotNull(temp1_score) AND temp1_score != '', toFloat32(temp1_score), -1) AS t1_score,
                if(isNotNull(temp2_score) AND temp2_score != '', toFloat32(temp2_score), -1) AS t2_score,
                if(isNotNull(temp3_score) AND temp3_score != '', toFloat32(temp3_score), -1) AS t3_score,
                if(isNotNull(temp4_score) AND temp4_score != '', toFloat32(temp4_score), -1) AS t4_score,
                if(isNotNull(temp5_score) AND temp5_score != '', toFloat32(temp5_score), -1) AS t5_score,
                if(isNotNull(original_score) AND original_score != '', toFloat32(original_score), -1) AS og_score
            SELECT
                AVG(t1_score),AVG(t2_score),AVG(t3_score),AVG(t4_score),AVG(t5_score),AVG(og_score)
            FROM events
                WHERE AIModel = '{model_id}'
                AND ModelVersion = '{version}'
                AND Environment = {environment}
                AND has(Features.Key,'node_id')
                AND deleted = 0
                AND t1_score != -1
                AND t2_score != -1
                AND t3_score != -1
                AND t4_score != -1
                AND t5_score != -1
                AND og_score != -1
            GROUP BY AIModel
            """
        return query

    def construct_dynamic_query(
        self,
        model_id: str,
        optimization_id: str,
        environment: int,
        version: str,
        metric_id: str,
        num_templates: int,
    ) -> str:
        """
        Constructs a dynamic SQL query to handle a variable number of template_scores.

        Parameters:
        - model_id (str): The ID of the AI model.
        - version (str): The version of the model.
        - environment (int): The environment ID.
        - num_scores (int): The number of k_scores to include in the query.

        Returns:
        - str: The dynamically constructed SQL query.
        """

        # Create dynamic score columns, conditions, and AVG functions
        score_columns: list[str] = []
        score_conditions: list[str] = []
        avg_scores: list[str] = []

        for i in range(num_templates):
            score_columns.append(
                f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_{i}_score')) AS temp{i + 1}_score"
            )
            score_conditions.append(
                f"if(isNotNull(temp{i + 1}_score) AND temp{i + 1}_score != '', toFloat32(temp{i + 1}_score), -1) AS t{i + 1}_score"
            )
            avg_scores.append(f"AVG(t{i + 1}_score) AS avg_t{i + 1}_score")

        # Include the original score column and condition
        score_columns.append(
            f"arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'optimized_{optimization_id}_{metric_id}_original')) AS original_score"
        )
        score_conditions.append(
            "if(isNotNull(original_score) AND original_score != '', toFloat32(original_score), -1) AS og_score"
        )
        avg_scores.append("AVG(og_score) AS avg_og_score")

        # Construct the SQL query dynamically
        query = f"""
        WITH
            {", ".join(score_columns)},
            {", ".join(score_conditions)}
        SELECT
            {", ".join(avg_scores)}
        FROM events
        WHERE AIModel = '{model_id}'
        AND ModelVersion = '{version}'
        AND Environment = {environment}
        AND has(Features.Key, 'node_id')
        AND deleted = 0
        AND {" AND ".join([f"t{i + 1}_score != -1" for i in range(num_templates)])}
        AND og_score != -1
        GROUP BY AIModel
        """

        return query

    @validated_request(
        request_serializer=ModelHubEmptyRequestSerializer,
        responses={
            200: OptimizeDatasetTemplateResultsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, model_id, optimization_id, *args, **kwarg):
        try:
            model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
            optimization = get_object_or_404(
                scoped_optimize_dataset_queryset(request).filter(model=model),
                id=optimization_id,
            )

            num_templates = len(optimization.optimized_k_prompts)

            results = []
            clickhouse_client = ClickHouseClientSingleton()

            for metric in optimization.metrics.all():
                dynamic_query = self.construct_dynamic_query(
                    model_id,
                    optimization_id,
                    _clickhouse_environment_value(optimization.environment),
                    optimization.version,
                    metric.id,
                    num_templates,
                )

                query_results = _execute_clickhouse_read_or_empty(
                    clickhouse_client,
                    dynamic_query,
                    operation="prompt_template_result",
                )
                if not query_results:
                    continue
                res = query_results[0]

                scores = res[0:-1]
                og_score = res[-1]

                results.append(
                    {
                        "metric_name": metric.name,
                        "templates": scores,
                        "old_template": og_score,
                    }
                )

            return Response(
                {
                    "k_prompts": optimization.optimized_k_prompts or [],
                    "results": results,
                },
                status=200,
            )
        except Http404:
            raise NotFound("Optimization not found.") from None
        except Exception as e:
            return GeneralMethods().internal_server_error_response(str(e))


class TemplateExploreView(APIView):
    permission_classes = [IsAuthenticated]

    def construct_query(
        self,
        model_id,
        environment,
        version,
        start_date,
        end_date,
        limit=10,
        offset=0,
        filters=None,
        sort_key="createdAt",
        sort_order="desc",
    ):
        # filter_query = self.get_filter_query(filters)

        # sort_query = self.get_order_query(sort_key, sort_order)

        if filters is None:
            filters = []
        query = f"""
            SELECT
                UUID,
                EventDateTime,
                EvalResults.Key,
                EvalResults.Value,
                arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS input_node_id,
                arrayElement(PredictionLabel.Value, indexOf(PredictionLabel.Key, 'node_id')) AS output_node_id
            FROM events
                WHERE AIModel = '{model_id}'
                AND ModelVersion = '{version}'
                AND Environment = {environment}
                AND has(Features.Key,'node_id')
                AND deleted = 0
                AND EventDateTime > toDateTime('{start_date}')
                AND  EventDateTime < toDateTime('{end_date}')
            LIMIT {limit} OFFSET {offset}
        """

        return query

    def get_order_query(
        self,
        sort_key="created_at",
        sort_order="desc",
    ):
        if sort_key in ("created_at", "createdAt"):
            return f" ORDER BY EventDateTime {sort_order} "
        else:
            return f""" ORDER BY
            CASE
                WHEN arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{sort_key}_score')) = '' THEN 0
                WHEN isNaN(toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{sort_key}_score')))) THEN 0
                ELSE toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{sort_key}_score')))
            END
            {sort_order} """

    def get_sql_based_on_operator(self, filter, column_name):
        operator = filter["operator"]
        first_value = filter["value"][0]
        second_value = filter["value"][1] if len(filter["value"]) > 1 else None

        if type(first_value) is str:
            first_value = f" '{first_value}' "

        if type(second_value) is str:
            second_value = f" '{second_value}' "

        if operator == "between":
            return (
                f" {column_name} > {first_value} AND  {column_name} < {second_value} "
            )

        elif operator == "notBetween":
            return (
                f" {column_name} <= {first_value} AND  {column_name} >= {second_value} "
            )

        elif operator == "equal":
            return f" {column_name} = {first_value} "
        elif operator == "notEqual":
            return f" {column_name} != {first_value} "
        elif operator == "greaterThan":
            return f" {column_name} > {first_value} "
        elif operator == "greaterThanEqualTo":
            return f" {column_name} >= {first_value} "
        elif operator == "lessThan":
            return f" {column_name} < {first_value} "
        elif operator == "lessThanEqualTo":
            return f" {column_name} <= {first_value} "

    def get_filter_query(self, filters=None):
        if filters is None:
            filters = []
        filter_arr = []

        metric_filters = [f for f in filters if f["key"] == "metric"]
        score_filters = [f for f in filters if f["key"] == "score"]
        create_at_filters = [
            f for f in filters if f["key"] in ("created_at", "createdAt")
        ]

        if len(metric_filters) > 0 and len(score_filters) > 0:
            # Apply these filter if there exists a score and metric
            for metric in metric_filters:
                for score in score_filters:
                    filter_arr.append(
                        self.get_sql_based_on_operator(
                            score,
                            f""" CASE
                                WHEN arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric["value"][0]}_score')) = '' THEN 0
                                WHEN isNaN(toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric["value"][0]}_score')))) THEN 0
                                ELSE toFloat64OrNull(arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'metric_{metric["value"][0]}_score')))
                            END """,
                        )
                    )

        for create_at_filter in create_at_filters:
            filter_arr.append(
                self.get_sql_based_on_operator(create_at_filter, "EventDate")
            )

        filter_query = ""

        if len(filter_arr) > 0:
            filter_query = " AND " + " AND ".join(filter_arr)

        return filter_query

    def construct_count_query(
        self, model_id, environment, version, start_date, end_date, filters=None
    ):
        # filter_query = self.get_filter_query(filters)

        if filters is None:
            filters = []
        query = f"""
            SELECT
                COUNT(*)
            FROM events
                WHERE AIModel = '{model_id}'
                AND ModelVersion = '{version}'
                AND Environment = {environment}
                AND has(Features.Key,'node_id')
                AND EventDateTime > toDateTime('{start_date}')
                AND  EventDateTime < toDateTime('{end_date}')
                AND deleted = 0
        """

        return query

    def extract_content_from_msg(self, content):
        result = []
        if content["content_type"] == "text":
            result = content["parts"][0]
        else:
            for model_input in content["parts"]:
                msg = {}

                if "image_url" in model_input["type"]:
                    msg["url"] = model_input.get("image_url", {}).get("url", "")

                if "text" in model_input["type"]:
                    msg["text"] = model_input.get("text", "")

                result.append(msg)

        return result

    @validated_request(
        request_serializer=OptimizeDatasetPageRequestSerializer,
        responses={
            200: OPTIMIZE_DATASET_DYNAMIC_ROWS_RESPONSE_SCHEMA,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, model_id, optimization_id):
        payload = request.validated_data

        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        optimization = get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )

        page = payload["page"]
        limit = payload["limit"]
        offset = (int(page) - 1) * limit
        environment = _clickhouse_environment_value(optimization.environment)
        version = optimization.version
        start_date = optimization.start_date.strftime("%Y-%m-%d %H:%M:%S")
        end_date = optimization.end_date.strftime("%Y-%m-%d %H:%M:%S")
        k_prompts = optimization.optimized_k_prompts

        # filters = request.data["filters"] or []
        # sort_order = request.data["sort_order"]
        # sort_key = request.data["sort_key"]

        clickhouse_client = ClickHouseClientSingleton()
        raw_data_points = _execute_clickhouse_read_or_empty(
            clickhouse_client,
            self.construct_query(
                model_id,
                environment,
                version,
                start_date,
                end_date,
                limit,
                offset,
            ),
            operation="prompt_template_explore_rows",
        )

        data_points_count = _execute_clickhouse_read_or_empty(
            clickhouse_client,
            self.construct_count_query(
                model_id, environment, version, start_date, end_date
            ),
            operation="prompt_template_explore_count",
        )

        total_count = int(data_points_count[0][0]) if data_points_count else 0

        total_pages = (total_count + limit - 1) // limit

        model_input_ids = []
        model_output_ids = []

        for p in raw_data_points:
            model_input_ids.append(p[4])
            model_output_ids.append(p[5])

        order = Case(
            *[When(id=val, then=pos) for pos, val in enumerate(model_input_ids)]
        )
        node_message_input = (
            Node.objects.filter(id__in=model_input_ids)
            .prefetch_related(
                Prefetch("message", queryset=Message.objects.only("content"))
            )
            .order_by(order)
        )

        order = Case(
            *[When(id=val, then=pos) for pos, val in enumerate(model_output_ids)]
        )
        node_message_output = (
            Node.objects.filter(id__in=model_output_ids)
            .prefetch_related(
                Prefetch("message", queryset=Message.objects.only("content"))
            )
            .order_by(order)
        )

        # metrics = get_metric_columns(model_id, dataset)

        formatted_data_points = []
        idx_node = 0

        # print("BLA", model_input_ids)

        for idx in range(len(raw_data_points)):
            try:
                each_data_point = raw_data_points[idx]
                obj = {
                    "id": each_data_point[0],
                    "input": node_message_input[idx_node].message.content["parts"][0],
                    "output": node_message_output[idx_node].message.content["parts"][0],
                }
            except (ValueError, IndexError):
                obj = {
                    "id": None,
                    "input": None,
                    "output": None,
                }
            # obj["input"] = self.extract_content_from_msg(
            #     node_message_input[idx_node].message.content
            # )

            # obj["past_input"] = Node.objects.get_all_parent_messages(
            #     model_input_ids[idx], self.extract_content_from_msg
            # )[:-1]

            # obj["output"] = self.extract_content_from_msg(
            #     node_message_output[idx_node].message.content
            # )

            for metric in optimization.metrics.all():
                # print("KPROMPTS", optimization.optimized_k_prompts)

                if not k_prompts:
                    break

                for idx in range(len(k_prompts)):
                    try:
                        score_idx = each_data_point[2].index(
                            f"optimized_{optimization_id}_{metric.id}_{idx}_score"
                        )
                        score = float(each_data_point[3][score_idx])
                    except (ValueError, IndexError):
                        score = None

                    obj[f"{str(metric.id)}-{idx}"] = score

                try:
                    idx = each_data_point[2].index(
                        f"optimized_{optimization_id}_{metric.id}_original",
                    )
                    score = float(each_data_point[3][idx])
                except (ValueError, IndexError):
                    score = None

                obj[f"{str(metric.id)}-original"] = score

            formatted_data_points.append(obj)

            idx_node += 1

        return Response(
            {
                "results": formatted_data_points,
                "count": total_count,
                "total_pages": total_pages,
                "current_page": page,
                "next": page < total_pages,
                "previous": page > 1,
                "message": "success",
            }
        )


class OptimizeDatasetColumnConfig(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: OptimizeDatasetColumnConfigResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, model_id):
        get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        user_organization = request_organization(request)
        user_workspace = request_workspace(request)

        column_config = scoped_column_config_for_identifier(
            request,
            ColumnConfig.TableName.OPTIMIZE_DATASET,
            f"{model_id}",
        )
        if column_config is None:
            column_config = ColumnConfig.objects.create(
                table_name=ColumnConfig.TableName.OPTIMIZE_DATASET,
                organization=user_organization,
                workspace=user_workspace,
                identifier=f"{model_id}",
                columns=optimize_table_columns,
            )

        column_serializer = ColumnConfigSerializer(column_config)

        return Response(
            {"columns": column_serializer.data["columns"], "status": "Success"}
        )

    @validated_request(
        request_serializer=OptimizeDatasetColumnConfigUpdateRequestSerializer,
        responses={
            200: OptimizeDatasetColumnConfigUpdateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, model_id):
        payload = request.validated_data

        get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        user_organization = request_organization(request)
        user_workspace = request_workspace(request)

        column_config = scoped_column_config_for_identifier(
            request,
            ColumnConfig.TableName.OPTIMIZE_DATASET,
            f"{model_id}",
        )
        if column_config is None:
            column_config = ColumnConfig.objects.create(
                table_name=ColumnConfig.TableName.OPTIMIZE_DATASET,
                organization=user_organization,
                workspace=user_workspace,
                identifier=f"{model_id}",
                columns=[],
            )

        column_config.columns = payload["columns"]

        column_config.save()

        return Response({"message": "Columns updated", "status": "Success"})


class OptimizeDatasetRightColumnConfig(APIView):
    permission_classes = [IsAuthenticated]

    def merge_metric_cols(self, existing_cols, metrics):
        input_dict = []
        for col in existing_cols:
            input_dict.append(col["value"])

        for metric in metrics:
            if f"{str(metric.id)}-old" not in input_dict:
                # Add the new metric if it doesn't exist in the input array
                existing_cols.append(
                    {
                        "label": f"(Old) {metric.name}",
                        "value": f"{str(metric.id)}-old",
                        "enabled": True,
                    }
                )
            if f"{str(metric.id)}-new" not in input_dict:
                # Add the new metric if it doesn't exist in the input array
                existing_cols.append(
                    {
                        "label": f"(New) {metric.name}",
                        "value": f"{str(metric.id)}-new",
                        "enabled": True,
                    }
                )

        return existing_cols

    @swagger_auto_schema(
        responses={
            200: OptimizeDatasetColumnConfigResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(
        self,
        request,
        model_id,
        optimization_id,
    ):
        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        user_organization = request_organization(request)
        user_workspace = request_workspace(request)

        optimization = get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )

        metrics = optimization.metrics.all()

        identifier = f"{model_id}-{optimization_id}-right-answers-explore"
        column_config = scoped_column_config_for_identifier(
            request,
            ColumnConfig.TableName.OPTIMIZE_DATASET_RIGHT_ANSWER,
            identifier,
        )
        if column_config is None:
            columns = get_right_answer_columns(metrics)
            column_config = ColumnConfig.objects.create(
                table_name=ColumnConfig.TableName.OPTIMIZE_DATASET_RIGHT_ANSWER,
                organization=user_organization,
                workspace=user_workspace,
                identifier=identifier,
                columns=columns,
            )
        else:
            column_config.columns = self.merge_metric_cols(
                column_config.columns, metrics
            )

        column_serializer = ColumnConfigSerializer(column_config)

        return Response(
            {"columns": column_serializer.data["columns"], "status": "Success"}
        )

    @validated_request(
        request_serializer=OptimizeDatasetColumnConfigUpdateRequestSerializer,
        responses={
            200: OptimizeDatasetColumnConfigUpdateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(
        self,
        request,
        model_id,
        optimization_id,
    ):
        payload = request.validated_data

        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )
        user_organization = request_organization(request)
        user_workspace = request_workspace(request)

        identifier = f"{model_id}-{optimization_id}-right-answers-explore"
        column_config = scoped_column_config_for_identifier(
            request,
            ColumnConfig.TableName.OPTIMIZE_DATASET_RIGHT_ANSWER,
            identifier,
        )
        if column_config is None:
            column_config = ColumnConfig.objects.create(
                table_name=ColumnConfig.TableName.OPTIMIZE_DATASET_RIGHT_ANSWER,
                organization=user_organization,
                workspace=user_workspace,
                identifier=identifier,
                columns=[],
            )

        column_config.columns = payload["columns"]

        column_config.save()

        return Response({"message": "Columns updated", "status": "Success"})


class OptimizeDatasetPromptExploreColumnConfig(APIView):
    permission_classes = [IsAuthenticated]

    def merge_metric_cols(self, existing_cols, metrics, k_prompts):
        input_dict = []
        for col in existing_cols:
            input_dict.append(col["value"])

        for metric in metrics:
            for idx in range(len(k_prompts)):
                if f"{str(metric.id)}-{idx}" not in input_dict:
                    # Add the new metric if it doesn't exist in the input array
                    existing_cols.append(
                        {
                            "label": f"(T{idx + 1}) {metric.name}",
                            "value": f"{str(metric.id)}-{idx}",
                            "enabled": True,
                        }
                    )
            if f"{str(metric.id)}-original" not in input_dict:
                # Add the new metric if it doesn't exist in the input array
                existing_cols.append(
                    {
                        "label": f"(Original) {metric.name}",
                        "value": f"{str(metric.id)}-original",
                        "enabled": True,
                    }
                )

        return existing_cols

    @swagger_auto_schema(
        responses={
            200: OptimizeDatasetColumnConfigResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(
        self,
        request,
        model_id,
        optimization_id,
    ):
        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        user_organization = request_organization(request)
        user_workspace = request_workspace(request)

        optimization = get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )

        metrics = optimization.metrics.all()

        k_prompts = optimization.optimized_k_prompts or []

        identifier = f"{model_id}-{optimization_id}-prompt-template-explore"
        column_config = scoped_column_config_for_identifier(
            request,
            ColumnConfig.TableName.OPTIMIZE_DATASET_PROMPT_TEMPLATE_EXPLORE,
            identifier,
        )
        if column_config is None:
            columns = get_prompt_template_columns(metrics, k_prompts)
            column_config = ColumnConfig.objects.create(
                table_name=ColumnConfig.TableName.OPTIMIZE_DATASET_PROMPT_TEMPLATE_EXPLORE,
                organization=user_organization,
                workspace=user_workspace,
                identifier=identifier,
                columns=columns,
            )
        else:
            column_config.columns = self.merge_metric_cols(
                column_config.columns, metrics, k_prompts
            )

        column_serializer = ColumnConfigSerializer(column_config)

        return Response(
            {"columns": column_serializer.data["columns"], "status": "Success"}
        )

    @validated_request(
        request_serializer=OptimizeDatasetColumnConfigUpdateRequestSerializer,
        responses={
            200: OptimizeDatasetColumnConfigUpdateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(
        self,
        request,
        model_id,
        optimization_id,
    ):
        payload = request.validated_data

        model = get_object_or_404(scoped_ai_model_queryset(request), id=model_id)
        get_object_or_404(
            scoped_optimize_dataset_queryset(request).filter(model=model),
            id=optimization_id,
        )
        user_organization = request_organization(request)
        user_workspace = request_workspace(request)

        identifier = f"{model_id}-{optimization_id}-prompt-template-explore"
        column_config = scoped_column_config_for_identifier(
            request,
            ColumnConfig.TableName.OPTIMIZE_DATASET_PROMPT_TEMPLATE_EXPLORE,
            identifier,
        )
        if column_config is None:
            column_config = ColumnConfig.objects.create(
                table_name=ColumnConfig.TableName.OPTIMIZE_DATASET_PROMPT_TEMPLATE_EXPLORE,
                organization=user_organization,
                workspace=user_workspace,
                identifier=identifier,
                columns=[],
            )

        column_config.columns = payload["columns"]

        column_config.save()

        return Response({"message": "Columns updated", "status": "Success"})


class OptimizedDatasetKbView(CreateAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=OptimizeDatasetKnowledgeBaseRequestSerializer,
        responses={
            200: OptimizeDatasetKnowledgeBaseCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        try:
            data = request.validated_data
            name = data.get("name")
            knowledge_base_metrics = data.get("knowledge_base_metrics")
            knowledge_base_filters = data.get("knowledge_base_filters")
            prompt = data.get("prompt")
            variables = data.get("variables")

            optim_dict = {
                "name": name,
                "optimize_type": OptimizeDataset.OptimizeType.RAG_TEMPLATE,
                "prompt": prompt,
                "knowledge_base_filters": knowledge_base_filters,
                "knowledge_base_metrics": knowledge_base_metrics,
                "variables": variables,
                "status": OptimizeDataset.StatusType.RUNNING.value,
                "organization": request_organization(request),
                "workspace": request_workspace(request),
                "environment": OptimizeDataset.EnvTypes.CORPUS,
                "version": "",
            }

            optim_obj = OptimizeDataset(**optim_dict)
            optim_obj.save()

            optimize_dataset = optim_obj
            if len(optimize_dataset.criteria_breakdown) != 0:
                criteria_breakdown = optimize_dataset.criteria_breakdown
            else:
                input_eval_prompts = knowledge_base_metrics
                metric = "\n".join(
                    [
                        f"{i}. {criteria}"
                        for i, criteria in enumerate(input_eval_prompts)
                    ]
                )
                criteria_breakdown = create_criteria_text_prompt(metric)
                optimize_dataset.criteria_breakdown = criteria_breakdown
                optimize_dataset.save()

            rag_prompt_optimzer.apply_async(args=(optim_obj.id,))

            return self._gm.success_response(optim_obj.id)
        except Exception as e:
            logger.exception(f"Error in starting knowledge base optimizer: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to Start knowledge base optimizer {get_error_message('FAILED_TO_START_KB_OPTIMIZER')}"
            )


@temporal_activity(time_limit=3600, queue="default")
def rag_prompt_optimzer(optim_id):
    optim_obj = OptimizeDataset.objects.filter(id=optim_id).get()
    time.sleep(10)
    prompt_optim_agent = PromptOptimizer(
        prompt=optim_obj.prompt,
        train_data="",
        criteria_breakdown=optim_obj.criteria_breakdown,
        optimize_rag=True,
        filter_keys=optim_obj.knowledge_base_filters,
        variables=optim_obj.variables,
    )
    top_k_optimized_prompts = prompt_optim_agent.get_optimized_prompt()

    optimized_prompts = [
        instruction_template["instruction_template"]
        for instruction_template in top_k_optimized_prompts
    ]
    optim_obj.optimized_k_prompts = optimized_prompts
    optim_obj.status = OptimizeDataset.StatusType.COMPLETED
    optim_obj.save()


class OptimizeDatasetList(ListAPIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = OptimizeDatasetKbSerializer
    queryset = OptimizeDataset.objects.all()

    @swagger_auto_schema(
        responses={
            200: OptimizeDatasetKnowledgeBaseListResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def list(self, request, *args, **kwargs):
        try:
            # Fetch all OptimizeDataset instances
            queryset = scoped_optimize_dataset_queryset(request).filter(
                optimize_type=OptimizeDataset.OptimizeType.RAG_TEMPLATE
            )
            serializer = self.get_serializer(queryset, many=True)

            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception(f"Error in starting knowledge base optimizer: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to Start knowledge base optimizer {get_error_message('FAILED_TO_START_KB_OPTIMIZER')}"
            )


class OptimizeDatasetGet(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: OptimizeDatasetKnowledgeBaseDetailResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, optim_id, *args, **kwargs):
        try:
            optimize_dataset = get_object_or_404(
                scoped_optimize_dataset_queryset(request).filter(
                    optimize_type=OptimizeDataset.OptimizeType.RAG_TEMPLATE
                ),
                id=optim_id,
            )
            data = {
                "name": optimize_dataset.name,
                "prompt": optimize_dataset.prompt,
                "knowledge_base_filters": optimize_dataset.knowledge_base_filters,
                "knowledge_base_metrics": optimize_dataset.knowledge_base_metrics,
                "variables": optimize_dataset.variables,
                "status": optimize_dataset.status,
                "optimized_k_prompts": optimize_dataset.optimized_k_prompts,
            }

            return self._gm.success_response(data)
        except Http404:
            raise NotFound("Optimization not found.") from None
        except Exception as e:
            logger.exception(f"Error in starting knowledge base optimizer: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to Start knowledge base optimizer {get_error_message('FAILED_TO_START_KB_OPTIMIZER')}"
            )
