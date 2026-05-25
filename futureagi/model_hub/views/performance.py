import csv
import traceback

import structlog


def _escape_csv_cell(value):
    """Defang CSV-formula injection: prefix a leading formula trigger with a
    single quote so spreadsheet apps (Excel/Sheets/LibreOffice) treat the cell
    as a literal string rather than evaluating it as a formula. Triggers per
    OWASP CSV-injection guidance: =, +, -, @, tab, carriage return.
    """
    if value is None:
        return ""
    s = str(value)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + s
    return s
from django.db.models import Case, Prefetch, When
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from model_hub.models import DatasetProperties
from model_hub.models.ai_model import AIModel
from model_hub.models.conversations import Conversation, Message, Node
from model_hub.models.metric import Metric
from model_hub.serializers.dataset_properties import (
    DatasetPropertiesDetailsSerializer,
)
from model_hub.serializers.metric import MetricSerializerNameAndId
from model_hub.utils.performance_ch import (
    calculate_performance_details,
    get_all_tags_distribution,
    get_performance_details_query,
    get_top_tags_distribution,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class PerformanceView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request, id, *args, **kwargs):
        user = request.user
        user_organization = user.organization

        model = AIModel.objects.get(id=id)

        if not model:
            return self._gm.not_found(get_error_message("AI_MODEL_NOT_FOUND"))

        datasets = request.data.get("datasets")
        filters = request.data.get("filters")
        breakdown = request.data.get("breakdown")
        agg_by = request.data.get("agg_by")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")

        data = {}

        for datasetIdx in range(len(datasets)):
            dataset = datasets[datasetIdx]

            environment = dataset["environment"]
            version = dataset["version"]

            all_options = DatasetProperties.objects.filter(
                organization=user_organization,
                model=model,
                environment=environment,
                version=version,
            ).values("name", "values")

            details = {}
            if len(breakdown) > 0:
                for breakdown_option in breakdown:
                    breakdown_key = breakdown_option["key"]

                    selected_property = all_options.filter(name=breakdown_key).first()
                    if not selected_property or not selected_property["values"]:
                        continue
                    for property_value in selected_property["values"]:
                        extra_filter = {
                            "operator": "equal",
                            "values": [property_value],
                            "type": "property",
                            "datatype": "string",
                            "key": breakdown_key,
                            "key_id": "",
                        }
                        details = get_performance_details_query(
                            user_organization.id,
                            model.id,
                            dataset,
                            filters
                            + [
                                extra_filter,
                            ]
                            + dataset["filters"],
                            agg_by,
                            start_date,
                            end_date,
                        )
                        data[f"Dataset {datasetIdx + 1}/{property_value}"] = details

            else:
                details = get_performance_details_query(
                    user_organization.id,
                    model.id,
                    dataset,
                    filters + dataset["filters"],
                    agg_by,
                    start_date,
                    end_date,
                )
                data[f"Dataset {datasetIdx + 1}"] = details

        return Response(data=data, status=status.HTTP_200_OK)


class PerformanceDetailsView(APIView):
    permission_classes = [IsAuthenticated]

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

    def post(self, request, id, *args, **kwargs):
        user = request.user
        organization = user.organization

        limit = 30
        page = int(request.data.get("page")) or 1
        offset = (int(page) - 1) * limit

        dataset = request.data.get("dataset")
        filters = request.data.get("filters")
        request.data.get("breakdown")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")

        metric_id = dataset["metric_id"]

        metric_model = Metric.objects.get(id=metric_id)

        if metric_model:
            is_next = False

            performance = calculate_performance_details(
                organization.id,
                id,
                dataset,
                filters,
                start_date,
                end_date,
                offset=offset,
                limit=limit,
            )

            # performance_count = calculate_performance_processing(
            #     organization.id, id, dataset, metric_model
            # )

            # log_count = performance_count[0][0]
            # processing_count = performance_count[0][1]

            log_count = 20
            processing_count = 0

            if len(performance) > limit:
                is_next = True
                performance = performance[0:limit]

            model_input_ids = []
            model_output_ids = []
            conversation_ids = []
            fetched_convs = []

            for p in performance:
                model_input_ids.append(p[4])
                model_output_ids.append(p[5])
                conversation_ids.append(p[-1])

            if metric_model.metric_type == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE:
                # Create a Case expression to preserve the order
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
                    *[
                        When(id=val, then=pos)
                        for pos, val in enumerate(model_output_ids)
                    ]
                )
                node_message_output = (
                    Node.objects.filter(id__in=model_output_ids)
                    .prefetch_related(
                        Prefetch("message", queryset=Message.objects.only("content"))
                    )
                    .order_by(order)
                )

            else:
                order = Case(
                    *[
                        When(id=val, then=pos)
                        for pos, val in enumerate(conversation_ids)
                    ]
                )
                conversations = (
                    Conversation.objects.filter(id__in=conversation_ids)
                    .prefetch_related(
                        Prefetch(
                            "nodes",
                            queryset=Node.objects.select_related("message"),
                        )
                    )
                    .order_by(order)
                )

                node_message_input = []
                node_message_output = []

                model_input_ids = []
                model_output_ids = []

                for conversation in conversations:
                    fetched_convs.append(conversation.nodes.all())
                    node_message_input.append(
                        fetched_convs[-1][len(fetched_convs[-1]) - 1]
                    )
                    node_message_output.append(
                        fetched_convs[-1][len(fetched_convs[-1]) - 2]
                    )

                    model_input_ids.append(node_message_input[-1].id)
                    model_output_ids.append(node_message_output[-1].id)

            formatted_performance = []
            idx_node = 0
            for idx in range(len(performance)):
                if (
                    metric_model.metric_type
                    == Metric.MetricTypes.STEPWISE_MODEL_INFERENCE
                ):
                    try:
                        if str(performance[idx][4]) != str(
                            node_message_input[idx_node]
                        ):
                            logger.info("missing id")
                            continue
                    except Exception as e:
                        traceback.print_exc()
                        logger.info(
                            f"Error: {str(e)}. Missing id: {performance[idx][4]}"
                        )
                        continue

                each_performance = performance[idx]
                rag_info = node_message_input[idx_node].message.content.get("rag_info")
                context = rag_info["context"] if rag_info.get("context") else None
                flattened_context = ""
                if context:
                    flattened_context = [
                        sentence for sublist in context for sentence in sublist
                    ]
                    flattened_context = " ".join(flattened_context)
                obj = {
                    "id": each_performance[0],
                    "model_input_type": node_message_input[idx_node].message.content[
                        "content_type"
                    ],
                    "model_output_type": node_message_output[idx_node].message.content[
                        "content_type"
                    ],
                    "model_input": node_message_input[idx_node].message.content,
                    "model_output": node_message_output[idx_node].message.content,
                    "score": each_performance[2],
                    "explanation": each_performance[3],
                    "date": each_performance[1],
                    "tags": each_performance[6].split(";"),
                    "context": flattened_context,
                    "variables": (
                        rag_info["variables"] if rag_info.get("variables") else {}
                    ),
                    "prompt_template": (
                        rag_info["prompt_template"]
                        if rag_info.get("prompt_template")
                        else None
                    ),
                }

                obj["model_input"] = self.extract_content_from_msg(
                    node_message_input[idx_node].message.content
                )

                obj["past_input"] = Node.objects.get_all_parent_messages(
                    model_input_ids[idx], self.extract_content_from_msg
                )[:-1]

                obj["model_output"] = self.extract_content_from_msg(
                    node_message_output[idx_node].message.content
                )

                formatted_performance.append(obj)

                idx_node += 1

            return Response(
                data={
                    "result": formatted_performance,
                    "processing_count": processing_count,
                    "count": log_count,
                    "is_next": is_next,
                    "page": page,
                },
                status=status.HTTP_200_OK,
            )

        else:
            response = {"dataset_a": {}}
            return Response(data=response, status=status.HTTP_200_OK)


class PerformanceDetailsExport(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, id, *args, **kwargs):
        user = request.user
        organization = user.organization

        dataset = request.data.get("dataset")

        metric = request.data.get("metric")

        if metric and "id" in metric:
            metric_model = Metric.objects.get(id=metric["id"])
        else:
            model = AIModel.objects.get(id=id)
            if model.default_metric:
                metric_model = model.default_metric.id

        if metric_model:
            performance = calculate_performance_details(
                organization.id,
                id,
                dataset,
                metric_model,
                unpaginated=True,
            )

            model_input_ids = []
            model_output_ids = []

            for p in performance:
                model_input_ids.append(p[4])
                model_output_ids.append(p[5])

            node_message_input = Node.objects.filter(
                id__in=model_input_ids
            ).prefetch_related(
                Prefetch("message", queryset=Message.objects.only("content"))
            )

            node_message_output = Node.objects.filter(
                id__in=model_output_ids
            ).prefetch_related(
                Prefetch("message", queryset=Message.objects.only("content"))
            )

            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="data.csv"'
            writer = csv.writer(response)
            writer.writerow(
                [
                    "Model Input",
                    "Model Output",
                    "Score",
                    "Explanation",
                    "Tags",
                    "Date",
                ]
            )
            for idx in range(len(performance)):
                each_performance = performance[idx]
                writer.writerow(
                    [
                        _escape_csv_cell(node_message_input[idx].message.content),
                        _escape_csv_cell(node_message_output[idx].message.content),
                        _escape_csv_cell(each_performance[2]),
                        _escape_csv_cell(each_performance[3]),
                        _escape_csv_cell(each_performance[6]),
                        _escape_csv_cell(each_performance[1]),
                    ]
                )

            return response
        else:
            return Response(
                data={},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class GetPerformanceOptionsView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def get(self, request, model_id, *args, **kwargs):
        user_organization = get_request_organization(self.request)
        search_query = request.query_params.get("search_query")
        metric_id = request.query_params.get("metric_id")

        models = AIModel.objects.filter(id=model_id)

        if len(models) == 0:
            return self._gm.not_found(get_error_message("AI_MODEL_NOT_FOUND"))

        model = models[0]

        metrics = Metric.objects.filter(
            model=model,
        )

        properties = DatasetProperties.objects.filter(
            organization=user_organization,
            model=model,
            deleted=False,
        )

        if search_query:
            metrics = metrics.filter(name__icontains=search_query)
            properties = properties.filter(name__icontains=search_query)

        properties = properties.distinct("name").order_by("name", "-id")

        for property in properties:
            # the below is becoming a list of lists, we need to flatten it
            values = DatasetProperties.objects.filter(
                name=property.name,
                organization=user_organization,
                model=model,
            ).values_list("values", flat=True)
            property.values = [item for sublist in values for item in sublist]

        tags = []

        if metric_id:
            metric = Metric.objects.filter(id=metric_id).values("tags").first()
            if metric and metric["tags"]:
                tags = metric["tags"]
                if search_query:
                    tags = [
                        tag
                        for tag in tags
                        if search_query.lower() in tag.split(":")[1].lower()
                    ]
            else:
                tags = []
        else:
            all_tags = Metric.objects.filter(model=model).values_list("tags", flat=True)
            tags = []
            for metric_tags in all_tags:
                tags.extend(metric_tags)
            tags = list(set(tags))  # Remove duplicates
            if search_query:
                tags = [
                    tag
                    for tag in tags
                    if search_query.lower() in tag.split(":")[1].lower()
                ]
        tags.sort()

        return self._gm.success_response(
            {
                "performance_metric": MetricSerializerNameAndId(
                    metrics, many=True
                ).data,
                "properties": DatasetPropertiesDetailsSerializer(
                    properties, many=True
                ).data,
                "meta_tags": [],
                "performance_tags": tags,
            }
        )


class GetPerformanceTagDistributionView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def post(self, request, model_id, *args, **kwargs):
        user_organization = get_request_organization(self.request)
        datasets = request.data.get("dataset")
        filters = request.data.get("filters")
        agg_by = request.data.get("agg_by")
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        graph_type = request.data.get("graph_type")

        if graph_type == "all":
            good_tags_distribution = get_all_tags_distribution(
                user_organization.id,
                model_id,
                datasets,
                filters,
                agg_by,
                start_date,
                end_date,
                "good",
            )

            bad_tags_distribution = get_all_tags_distribution(
                user_organization.id,
                model_id,
                datasets,
                filters,
                agg_by,
                start_date,
                end_date,
                "bad",
            )

            return self._gm.success_response(
                {
                    "good": good_tags_distribution,
                    "bad": bad_tags_distribution,
                }
            )
        else:
            top_tags_distribution = get_top_tags_distribution(
                user_organization.id,
                model_id,
                datasets,
                filters,
                start_date,
                end_date,
                graph_type,
            )

            return self._gm.success_response(top_tags_distribution)
