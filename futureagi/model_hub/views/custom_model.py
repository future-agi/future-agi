# views.py
import re
import traceback

import structlog
from django.db.models import F
from django.db.models.functions import Lower
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.utils import get_request_organization
from model_hub.models.api_key import ApiKey
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.metric import Metric
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    CustomAIModelBaselineRequestSerializer,
    CustomAIModelCreateRequestSerializer,
    CustomAIModelCreateResponseSerializer,
    CustomAIModelDefaultMetricRequestSerializer,
    CustomAIModelDeleteRequestSerializer,
    CustomAIModelEditRequestSerializer,
    CustomAIModelEditResponseSerializer,
    CustomAIModelUpdateRequestSerializer,
    ModelHubPaginatedResponseSerializer,
    ModelHubStatusMessageResponseSerializer,
    ModelHubStringResultResponseSerializer,
)
from model_hub.serializers.custom_models import (
    CustomAIModelSerializer,
    CustomAIModelsListSerializer,
)
from model_hub.utils.azure_endpoints import normalize_azure_custom_model_config
from model_hub.utils.clickhouse import get_model_volume
from model_hub.utils.utils import validate_model_working
from model_hub.utils.workspace_scope import request_workspace, request_workspace_filter
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


def _restore_plain_key_config_for_save(ai_model):
    if ai_model.key_config:
        ai_model.key_config = ai_model.actual_json


def _custom_ai_model_queryset(request):
    return CustomAIModel.no_workspace_objects.filter(
        organization=get_request_organization(request),
    ).filter(request_workspace_filter(request))


def _api_key_queryset(request):
    return ApiKey.no_workspace_objects.filter(
        organization=get_request_organization(request),
    ).filter(request_workspace_filter(request))


def _normalize_custom_model_environment(environment):
    if environment is None:
        return None
    canonical_values = {
        value.lower(): value for value, _label in CustomAIModel.EnvTypes.choices
    }
    return canonical_values.get(str(environment).lower(), environment)


class CustomAIModelView(APIView):
    """Return all the models that belongs to a user organization"""

    serializer_class = CustomAIModelSerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @staticmethod
    def _get_model_volume_or_default(model_id):
        try:
            return get_model_volume(model_ids=[model_id])
        except Exception as exc:
            logger.warning(
                "Error fetching custom model volume",
                model_id=str(model_id),
                error=str(exc),
            )
            return 0, 0

    @swagger_auto_schema(
        responses={
            200: ModelHubPaginatedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        sort_order = request.query_params.get("sort_order")
        search_query = request.query_params.get("search_query")

        ai_models = _custom_ai_model_queryset(request).order_by("-created_at")

        if sort_order:
            if sort_order == "asc":
                ai_models = ai_models.order_by(Lower("user_model_id"))
            elif sort_order == "desc":
                ai_models = ai_models.order_by(Lower(F("user_model_id")).desc())

        if search_query:
            pattern = rf"(?i){re.escape(search_query)}"
            ai_models = ai_models.filter(user_model_id__regex=pattern)

        paginator = ExtendedPageNumberPagination()
        result_page = paginator.paginate_queryset(ai_models, request)
        result_page = CustomAIModelSerializer(result_page, many=True).data

        api_keys_all = _api_key_queryset(request).filter(
            provider__in=[res.get("provider") for res in result_page],
        )
        provider_key_map = {res.provider: res for res in api_keys_all}
        for res in result_page:
            res["volume"], res["total_count"] = self._get_model_volume_or_default(
                res["id"]
            )
            if not res.get("config_json"):
                try:
                    api_key = provider_key_map.get(res.get("provider"))

                    if api_key:
                        keys = api_key.masked_actual_key
                        res.update(
                            {
                                "config_json": (
                                    {"key": keys} if isinstance(keys, str) else keys
                                )
                            }
                        )
                except Exception as e:
                    # Log the error but don't break the response
                    logger.warning(
                        f"Error fetching API keys for model {res.get('id')}: {e}"
                    )

            if res["provider"].startswith("custom_"):
                res["provider"] = "custom"

        return paginator.get_paginated_response(list(result_page))


class CustomAIModelCreateView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=CustomAIModelCreateRequestSerializer,
        responses={
            200: CustomAIModelCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.validated_data
            model_provider = data.get("model_provider")
            model_name = data.get("model_name")
            input_token_cost = data.get("input_token_cost")
            output_token_cost = data.get("output_token_cost")
            config_json = data.get("config_json", {})
            key = data.get("key", None)
            user_organization = get_request_organization(self.request)

            if not model_provider:
                return self._gm.bad_request("Model Provider Required!")

            if (
                str(model_provider).strip().lower() == "bedrock"
                or str(model_provider).strip().lower() == "sagemaker"
            ):
                if (
                    not all(
                        key in config_json
                        for key in [
                            "aws_access_key_id",
                            "aws_secret_access_key",
                            "aws_region_name",
                        ]
                    )
                ) and (not key):
                    return self._gm.bad_request(get_error_message("MISSING_AWS_KEY"))
                if not key:
                    api_key_params = {
                        "aws_access_key_id": config_json["aws_access_key_id"],
                        "aws_secret_access_key": config_json["aws_secret_access_key"],
                        "aws_region_name": config_json["aws_region_name"],
                    }
                else:
                    api_key_params = {"key": key}

            elif str(model_provider).strip().lower() == "azure":
                normalized = normalize_azure_custom_model_config(config_json)
                required_keys = ["api_base", "api_key"]
                if normalized.get("azure_endpoint_type") != "foundry":
                    required_keys.append("api_version")
                if (
                    not all(
                        normalized.get(required_key) for required_key in required_keys
                    )
                ) and (not key):
                    return self._gm.bad_request(get_error_message("MISSING_AZURE_KEY"))
                if not key:
                    api_key_params = {
                        "api_base": normalized["api_base"],
                        "api_key": normalized["api_key"],
                    }
                    if normalized.get("api_version"):
                        api_key_params["api_version"] = normalized["api_version"]
                    # Update config_json with normalized values and endpoint type
                    config_json = {
                        **config_json,
                        "api_base": normalized["api_base"],
                        "api_key": normalized["api_key"],
                        "azure_endpoint_type": normalized["azure_endpoint_type"],
                    }
                    if normalized.get("api_version"):
                        config_json["api_version"] = normalized["api_version"]
                else:
                    api_key_params = {"key": key}

            elif (str(model_provider).strip().lower() == "vertex_ai") or (
                str(model_provider).strip().lower() == "custom"
            ):
                if model_provider == "vertex_ai" and not model_name.startswith(
                    "vertex_ai/"
                ):
                    model_name = f"vertex_ai/{model_name}"
                if (not config_json) and (not key):
                    return self._gm.bad_request(get_error_message("MISSING_JSON_KEY"))
                if not key:
                    api_key_params = {
                        "config_json": config_json,
                    }
                else:
                    api_key_params = {"key": key}

            elif str(model_provider).strip().lower() == "openai":
                if not config_json:
                    return self._gm.bad_request(get_error_message("MISSING_OPENAI_KEY"))
                api_key_params = {"api_key": config_json["key"]}
                if config_json.get("api_base"):
                    api_key_params.update({"api_base": config_json["api_base"]})

            config_copy = config_json.copy()
            res = validate_model_working(
                model_name=model_name, api_key=api_key_params, provider=model_provider
            )
            if isinstance(res, Exception):
                return self._gm.bad_request(str(res))

            if (
                _custom_ai_model_queryset(request)
                .filter(
                    user_model_id=model_name,
                    provider=str(model_provider).strip().lower(),
                )
                .exists()
            ):
                return self._gm.bad_request(
                    get_error_message("MODEL_NAME_ALREADY_EXISTS")
                )

            model = CustomAIModel.objects.create(
                user_model_id=model_name,
                provider=str(model_provider).strip().lower(),
                input_token_cost=input_token_cost,
                output_token_cost=output_token_cost,
                organization=user_organization,
                workspace=request_workspace(request),
                key_config=config_copy,
                user=request.user,
                deleted=False,
            )

            return self._gm.success_response(
                {
                    "status": "success",
                    "message": f"{model_name} Model created successfully",
                    "data": {"id": model.id},
                }
            )
        except Exception as e:
            logger.exception(f"Erro in CustomAIModelCreateView: {e}")
            return self._gm.bad_request(get_error_message("UNABLE_TO_CREATE_MODEL"))


class CustomAIModelDetailsView(APIView):
    serializer_class = CustomAIModelSerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={200: CustomAIModelSerializer, **MODEL_HUB_ERROR_RESPONSES}
    )
    def get(self, request, id, *args, **kwargs):
        """Return details regarding a particular model, given his id"""
        ai_model = _custom_ai_model_queryset(request).filter(id=id).first()
        if not ai_model:
            return self._gm.not_found("Custom AI model not found")
        ai_model_serializer = CustomAIModelSerializer(ai_model)
        return Response({**ai_model_serializer.data})

    @validated_request(
        request_serializer=CustomAIModelUpdateRequestSerializer,
        responses={200: CustomAIModelSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, id, *args, **kwargs):
        """Update custom model details"""
        data = request.validated_data
        input_token_cost = data.get("input_token_cost")
        output_token_cost = data.get("output_token_cost")
        try:
            ai_model = _custom_ai_model_queryset(request).filter(id=id).first()
            if not ai_model:
                return self._gm.not_found("Custom AI model not found")
            new_model_name = data.get("model_name")
            if (
                new_model_name
                and _custom_ai_model_queryset(request)
                .filter(
                    user_model_id=new_model_name,
                )
                .exclude(id=id)
                .exists()
            ):
                return self._gm.bad_request(get_error_message("MODEL_NAME_IS_USED"))
            if input_token_cost is not None:
                ai_model.input_token_cost = input_token_cost
            if output_token_cost is not None:
                ai_model.output_token_cost = output_token_cost
            if new_model_name:
                ai_model.user_model_id = new_model_name
            _restore_plain_key_config_for_save(ai_model)
            ai_model.save()

            ai_model_serializer = CustomAIModelSerializer(ai_model)

            return Response({**ai_model_serializer.data}, status=200)

        except Exception as e:
            return Response(build_error_envelope(str(e), status_code=400), status=400)


class UpdateMetricCustomAIModelView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=CustomAIModelDefaultMetricRequestSerializer,
        responses={
            200: ModelHubStatusMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, id, *args, **kwargs):
        """Update default metric of the model given model id in request and information of metric in the body"""
        user_organization = get_request_organization(self.request)

        data = request.validated_data
        ai_model_id = id
        new_metrics_id = data.get("metric_id")

        try:
            ai_model = _custom_ai_model_queryset(request).get(id=ai_model_id)
            new_metrics = (
                Metric.objects.select_related("model")
                .filter(
                    id=new_metrics_id,
                    model_id=ai_model_id,
                    model__organization=user_organization,
                )
                .filter(
                    request_workspace_filter(request, field_name="model__workspace")
                )
                .get()
            )
            ai_model.default_metric = new_metrics
            _restore_plain_key_config_for_save(ai_model)
            ai_model.save()

            return Response(
                {
                    "status": "success",
                    "message": "Custom AI model updated successfully",
                }
            )

        except CustomAIModel.DoesNotExist:
            return GeneralMethods().not_found("Custom AI model not found")
        except Metric.DoesNotExist:
            return GeneralMethods().not_found("Metric not found")


class UpdateBaselineDatasetCustomAIModelView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=CustomAIModelBaselineRequestSerializer,
        responses={
            200: ModelHubStatusMessageResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request, id, *args, **kwargs):
        data = request.validated_data
        ai_model_id = id
        environment = _normalize_custom_model_environment(data.get("environment"))
        version = data.get("model_version")

        try:
            ai_model = _custom_ai_model_queryset(request).get(id=ai_model_id)
            ai_model.baseline_model_environment = environment
            ai_model.baseline_model_version = version
            _restore_plain_key_config_for_save(ai_model)
            ai_model.save()

            return Response(
                {
                    "status": "success",
                    "message": "AI model updated successfully",
                }
            )

        except CustomAIModel.DoesNotExist:
            return GeneralMethods().not_found("AI model not found")


class CustomAIModelListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: ModelHubPaginatedResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        search_query = request.query_params.get("search_query")
        limit = request.query_params.get("limit")

        ai_models = (
            _custom_ai_model_queryset(request)
            .values("id", "user_model_id")
            .order_by("-created_at")
        )

        if search_query:
            pattern = rf"(?i){re.escape(search_query)}"
            ai_models = ai_models.filter(user_model_id__regex=pattern)

        paginator = ExtendedPageNumberPagination()
        paginator.page_size = int(limit) if limit else 10
        result_page = paginator.paginate_queryset(ai_models, request)
        result_page = CustomAIModelsListSerializer(result_page, many=True).data

        return paginator.get_paginated_response(list(result_page))


class DeleteCustomAIModelView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=CustomAIModelDeleteRequestSerializer,
        responses={
            200: ModelHubStringResultResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def delete(self, request, *args, **kwargs):
        ai_model_ids = request.validated_data.get("ids", [])
        try:
            for model_id in ai_model_ids:
                # SECURITY: Only delete models belonging to user's organization
                _custom_ai_model_queryset(request).filter(
                    id=model_id,
                ).update(deleted=True, user_model_id=self._gm.generate_random_text())

            return self._gm.success_response("AI model deleted successfully")

        except CustomAIModel.DoesNotExist:
            return self._gm.bad_request("AI model not found")
        except Exception as e:
            return self._gm.bad_request(str(e))


class EditCustomModel(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: CustomAIModelEditResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        }
    )
    def get(self, request, *args, **kwargs):
        model_id = request.query_params.get("id", None)

        if not model_id:
            return self._gm.bad_request("Model ID is required.")
        try:
            model = _custom_ai_model_queryset(request).get(id=model_id)
            data = {
                "model_name": model.user_model_id,
                "input_token_cost": model.input_token_cost,
                "output_token_cost": model.output_token_cost,
                "model_provider": (
                    model.provider
                    if not model.provider.startswith("custom_")
                    else "custom"
                ),
            }
            # Try to get API key if it exists (may not exist for all models)
            api_keys = (
                _api_key_queryset(request).filter(provider=model.provider).first()
            )
            if api_keys:
                keys = api_keys.masked_actual_key
                data.update({"key" if api_keys.actual_key else "config_json": keys})
            return self._gm.success_response(data)

        except CustomAIModel.DoesNotExist:
            return self._gm.bad_request("Model not found")
        except Exception as e:
            traceback.print_exc()
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=CustomAIModelEditRequestSerializer,
        responses={
            200: ModelHubStringResultResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def patch(self, request, *args, **kwargs):
        model_id = request.validated_data.get("id", None)

        if not model_id:
            return self._gm.bad_request("Model ID is required")

        try:
            data = request.validated_data
            model_name = data.get("model_name", None)
            input_token_cost = data.get("input_token_cost", None)
            output_token_cost = data.get("output_token_cost", None)
            config_json = data.get("config_json", {})
            key = data.get("key", None)

            model = _custom_ai_model_queryset(request).get(id=model_id)
            if not model_name:
                model_name = model.user_model_id
            if key or config_json:
                try:
                    if key:
                        res = validate_model_working(
                            model_name=model_name,
                            api_key={"api_key": key},
                            provider=model.provider,
                        )
                    else:
                        validation_config = (
                            config_json.copy()
                            if isinstance(config_json, dict)
                            else config_json
                        )
                        res = validate_model_working(
                            model_name=model_name,
                            api_key=(
                                {"config_json": validation_config}
                                if model.provider in ["vertex_ai", "custom"]
                                else validation_config
                            ),
                            provider=model.provider,
                        )

                    if isinstance(res, Exception):
                        return self._gm.bad_request(
                            "Model_validation Failed. Please enter correct details."
                        )
                except Exception:
                    return self._gm.bad_request("Model Validation failed.")

            model.user_model_id = model_name if model_name else model.user_model_id
            if input_token_cost is not None:
                model.input_token_cost = input_token_cost
            if output_token_cost is not None:
                model.output_token_cost = output_token_cost
            if config_json:
                model.key_config = config_json
            elif key:
                model.key_config = {"key": key}
            else:
                _restore_plain_key_config_for_save(model)
            model.save()

            return self._gm.success_response("Model updated Successfully")

        except CustomAIModel.DoesNotExist:
            return self._gm.bad_request("AI model not found")
        except Exception as e:
            return self._gm.bad_request(str(e))
