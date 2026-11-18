import re

from django.db.models import Case, CharField, F, Value, When
from django.db.models.functions import Lower
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.prompt_eval_agent.prompt_eval import PromptValidator
except ImportError:
    PromptValidator = _ee_stub("PromptValidator")
from model_hub.models.metric import Metric
from model_hub.models.metric_prompt_checker import PromptChecker
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    CustomMetricListResponseSerializer,
    CustomMetricMutationRequestSerializer,
    CustomMetricTestRequestSerializer,
    CustomMetricTestResponseSerializer,
    MetricTagOptionSerializer,
    ModelHubPaginatedResponseSerializer,
    ModelHubStatusResponseSerializer,
)
from model_hub.serializers.metric import MetricSerializer
from model_hub.utils.utils import check_valid_metrics, get_evaluation_type
from model_hub.utils.workspace_scope import scoped_ai_model_queryset
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


def _get_scoped_model(request, model_id):
    return scoped_ai_model_queryset(request).filter(id=model_id).first()


def _scoped_metric_queryset(request):
    return Metric.objects.filter(
        model_id__in=scoped_ai_model_queryset(request).values("id")
    )


def _get_metric_type(metric_type):
    try:
        metric_type = int(metric_type)
    except (TypeError, ValueError):
        pass
    return Metric.MetricTypes.get_metric_type(metric_type)


class AllMetricApiView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={200: CustomMetricListResponseSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, model_id, *args, **kwargs):
        ai_model = _get_scoped_model(request, model_id)
        if not ai_model:
            return GeneralMethods().not_found("Model with given id not found.")

        all_metrics = (
            Metric.objects.filter(model=ai_model)
            .annotate(
                updated_evaluation_type=Case(
                    When(
                        evaluation_type=Metric.EvalMetricTypes.EVAL_CONTEXT,
                        then=Value("EVALUATE_CONTEXT"),
                    ),
                    When(
                        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
                        then=Value("EVALUATE_PROMPT_TEMPLATE"),
                    ),
                    When(
                        evaluation_type=Metric.EvalMetricTypes.EVAL_CONTEXT_RANKING,
                        then=Value("EVALUATE_CONTEXT_RANKING"),
                    ),
                    default=Value("EVALUATE_CHAT"),
                    output_field=CharField(),
                )
            )
            .values("id", "name", "updated_evaluation_type")
        )

        all_metrics = [
            {
                "id": metric["id"],
                "name": metric["name"],
                "evaluation_type": metric["updated_evaluation_type"],
            }
            for metric in all_metrics
        ]

        return Response(
            {"metrics": all_metrics},
            status=status.HTTP_200_OK,
        )


class MetricApiView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: ModelHubPaginatedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, model_id, *args, **kwargs):
        sort_order = request.query_params.get("sort_order")
        search_query = request.query_params.get("search_query")

        ai_model = _get_scoped_model(request, model_id)
        if not ai_model:
            return GeneralMethods().not_found("Model with given id not found.")

        # Add default ordering to the queryset
        all_metrics = Metric.objects.filter(model=ai_model).order_by("-created_at")

        if search_query:
            pattern = rf"(?i){re.escape(search_query)}"
            all_metrics = all_metrics.filter(name__regex=pattern)

        if sort_order:
            if sort_order == "asc":
                all_metrics = all_metrics.order_by(Lower("name"))
            elif sort_order == "desc":
                all_metrics = all_metrics.order_by(Lower(F("name")).desc())

        paginator = ExtendedPageNumberPagination()
        result_page = paginator.paginate_queryset(all_metrics, request)
        result_page = MetricSerializer(result_page, many=True).data

        for res in result_page:
            res["raw_datasets"] = res["datasets"]
            res["datasets"] = ", ".join(
                [
                    f"{item['environment']}:{item['model_version']}"
                    for item in res["datasets"]
                ]
            )
            res["description"] = ""

        return paginator.get_paginated_response(list(result_page))


class CreateMetricApiView(APIView):
    permission_classes = [IsAuthenticated]
    gm = GeneralMethods()

    @validated_request(
        request_serializer=CustomMetricMutationRequestSerializer,
        responses={200: ModelHubStatusResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        data = request.validated_data

        try:
            # Check if a non-deleted PromptChecker with the specified user prompt and no ambiguity exists
            # prompt_exists = PromptChecker.objects.filter(
            #     deleted=False,
            #     user_prompt=data.get(
            #         "prompt"
            #     ),  # Use .get() to safely access the dictionary key
            #     ambiguity=False,
            # ).exists()

            # # If the prompt exists, get the corresponding ai_prompt
            # if prompt_exists:
            #     ai_prompt = (
            #         PromptChecker.objects.filter(
            #             deleted=False,
            #             user_prompt=data["prompt"],
            #             ambiguity=False,
            #         )
            #         .values_list("ai_prompt", flat=True)
            #         .first()
            #     )
            # else:
            #     ai_prompt = None

            # Commenting as this is not stable
            # if not ai_prompt:
            #     agent = PromptValidator()
            #     ambiguity_result = agent.is_valid_prompt(data["prompt"])
            #     if ambiguity_result.get("is_ambiguity"):
            #         return Response(
            #             {
            #                 "status": "error",
            #                 "message": ambiguity_result.get("explanation"),
            #             },
            #             status=400,
            #         )

            ai_model = _get_scoped_model(request, data["model_id"])
            if not ai_model:
                return GeneralMethods().not_found("AI model not found")

            valid, msg = check_valid_metrics(data["evaluation_type"], ai_model.id)
            if not valid:
                return GeneralMethods().bad_request(msg)
            metric_type = _get_metric_type(data["metric_type"])
            if metric_type is None:
                return GeneralMethods().bad_request("Invalid metric_type")

            created_metric = Metric(
                name=data["name"],
                text_prompt=data["prompt"],
                model=ai_model,
                metric_type=metric_type,
                datasets=data["datasets"],
                evaluation_type=get_evaluation_type(data["evaluation_type"]),
                criteria_breakdown=[],
            )
            created_metric.save()

            return Response({"status": "success"})

        except KeyError as exc:
            return GeneralMethods().bad_request(f"{exc.args[0]} is required")


class EditMetricApiView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=CustomMetricMutationRequestSerializer,
        responses={200: ModelHubStatusResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        data = request.validated_data

        try:
            metric = (
                _scoped_metric_queryset(request)
                .select_related("model")
                .get(
                    id=data["id"],
                )
            )

            # Check if a non-deleted PromptChecker with the specified user prompt and no ambiguity exists
            # prompt_exists = PromptChecker.objects.filter(
            #     deleted=False,
            #     user_prompt=data.get(
            #         "prompt"
            #     ),  # Use .get() to safely access the dictionary key
            #     ambiguity=False,
            # ).exists()

            # # If the prompt exists, get the corresponding ai_prompt
            # if prompt_exists:
            #     ai_prompt = (
            #         PromptChecker.objects.filter(
            #             deleted=False,
            #             user_prompt=data["prompt"],
            #             ambiguity=False,
            #         )
            #         .values_list("ai_prompt", flat=True)
            #         .first()
            #     )
            # else:
            #     ai_prompt = None
            # if not ai_prompt:
            #     agent = PromptValidator()
            #     ambiguity_result = agent.is_valid_prompt(data["prompt"])
            #     if ambiguity_result.get("is_ambiguity"):
            #         return Response(
            #             {
            #                 "status": "error",
            #                 "message": ambiguity_result.get("explanation"),
            #             },
            #             status=400,
            #         )

            # new_metric_data = {
            #     "name": metric.name,
            #     "text_prompt": metric.text_prompt,
            #     "criteria_breakdown": [],
            #     "model": metric.model,
            #     "metric_type": metric.metric_type,
            #     "datasets": metric.datasets,
            #     "evaluation_type":get_evaluation_type(data["evaluation_type"]),
            # }

            metric_type = _get_metric_type(data["metric_type"])
            if metric_type is None:
                return GeneralMethods().bad_request("Invalid metric_type")

            new_metric_data = {
                "name": data["name"],
                "text_prompt": data["prompt"],
                "criteria_breakdown": [],
                "model": metric.model,
                "metric_type": metric_type,
                "datasets": data["datasets"],
                "evaluation_type": get_evaluation_type(data["evaluation_type"]),
            }

            # check if the all fields are same as the old metric exluding datasets
            metric_data_changed = False
            for key in new_metric_data:
                if key in ["text_prompt", "model", "metric_type", "evaluation_type"]:
                    if new_metric_data[key] != getattr(metric, key):
                        metric_data_changed = True
                        break

            if not metric_data_changed:
                metric.name = data["name"]
                metric.datasets = data["datasets"]
                metric.save()
                # print("-------------------------------------")
                # print("Updating the metric")
                return Response({"status": "success"})

            # print("-------------------------------------")
            # print("Deleting the old metric and Creating a new metric")

            # create a new metric and fill this object with the new data
            new_metric = Metric(**new_metric_data)
            new_metric.save()
            metric.delete()

            return Response({"status": "success"})

        except Metric.DoesNotExist:
            return GeneralMethods().not_found("Metric not found")


class GetMetricTagOptions(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: MetricTagOptionSerializer(many=True),
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, metric_id, *args, **kwargs):
        metric = (
            _scoped_metric_queryset(request).filter(id=metric_id).values("tags").first()
        )

        if metric is None:
            return GeneralMethods().not_found("Metric not found")

        tags = sorted(
            [{"label": tag, "value": tag} for tag in metric["tags"]],
            key=lambda d: d["value"],
        )

        # print(metric[0]["tags"])

        return Response(
            tags,
            status=status.HTTP_200_OK,
        )


class TestMetric(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=CustomMetricTestRequestSerializer,
        responses={
            200: CustomMetricTestResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        data = request.validated_data

        try:
            # Check if a non-deleted PromptChecker with the specified user prompt and no ambiguity exists
            prompt_exists = PromptChecker.objects.filter(
                deleted=False,
                user_prompt=data.get(
                    "prompt"
                ),  # Use .get() to safely access the dictionary key
                ambiguity=False,
            ).exists()

            # If the prompt exists, get the corresponding ai_prompt
            if prompt_exists:
                ai_prompt = (
                    PromptChecker.objects.filter(
                        deleted=False,
                        user_prompt=data["prompt"],
                        ambiguity=False,
                    )
                    .values_list("ai_prompt", flat=True)
                    .first()
                )
            else:
                ai_prompt = None
            if not ai_prompt:
                prompt = data["prompt"]
                agent = PromptValidator()
                ambiguity_result = agent.is_valid_prompt(prompt)

                PromptChecker.objects.create(
                    deleted=False,
                    explanation=ambiguity_result.get("explanation"),
                    user_prompt=prompt,
                    ai_prompt=ambiguity_result.get("prompts"),
                    ambiguity=ambiguity_result.get("is_ambiguity"),
                )

                # if ambiguity_result.get("is_ambiguity"):
                #     return Response(
                #         {
                #             "status": "error",
                #             "message": ambiguity_result.get("explanation"),
                #         },
                #         status=400,
                #     )

                return Response(
                    {
                        "status": "success",
                        "prompts": ambiguity_result.get("prompts"),
                    }
                )
            return Response({"status": "success", "prompts": ai_prompt})

        except APIException:
            raise
        except Exception as exc:
            return GeneralMethods().bad_request(str(exc))
