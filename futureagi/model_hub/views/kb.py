from typing import Any

import structlog
from django.db.models import Q
from django.http import Http404
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from model_hub.models.kb import KnowledgeBase
from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    KnowledgeBaseEmbeddingModelsResponseSerializer,
    KnowledgeBaseListResponseSerializer,
    KnowledgeBaseResponseSerializer,
)
from model_hub.serializers.kb import (
    KnowledgeBaseCreateSerializer,
    KnowledgeBaseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


class KnowledgeBaseViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    """
    ViewSet for handling KnowledgeBase operations.
    """

    queryset = KnowledgeBase.objects.all()
    permission_classes = [IsAuthenticated]
    pagination_class = ExtendedPageNumberPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ["name"]
    _gm: GeneralMethods = GeneralMethods()

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        # Apply additional search filtering
        search = self.request.query_params.get("search", None)
        if search:
            queryset = queryset.filter(Q(name__icontains=search))
        return queryset

    def get_serializer_class(self):
        if self.action == "create":
            return KnowledgeBaseCreateSerializer
        return KnowledgeBaseSerializer

    @validated_request(
        operation_description="Create a new knowledge base.",
        operation_summary="Create a new knowledge base.",
        request_serializer=KnowledgeBaseCreateSerializer,
        responses={201: KnowledgeBaseResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from tfc.ee_gating import EEFeature, check_ee_feature

        org = getattr(request, "organization", None) or request.user.organization
        check_ee_feature(EEFeature.KNOWLEDGE_BASE, org_id=str(org.id))

        try:
            serializer = self.get_serializer(data=request.validated_data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            return self._gm.success_response(serializer.data, status=201)
        except Exception as e:
            logger.exception(f"Error in creating the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to create knowledge base: {get_error_message('FAILED_TO_CREATE_KB')}"
            )

    @swagger_auto_schema(
        operation_description="List all knowledge bases.",
        operation_summary="List all knowledge bases.",
        responses={
            200: KnowledgeBaseListResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            response: Response = super().list(request, *args, **kwargs)
            return self._gm.success_response(response.data)
        except Exception as e:
            logger.exception(f"Error in fetching the knowledge bases: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to list knowledge bases: {get_error_message('FAILED_TO_LIST_KB')}"
            )

    @swagger_auto_schema(
        operation_description="Retrieve a specific knowledge base.",
        operation_summary="Retrieve a specific knowledge base.",
        responses={200: KnowledgeBaseResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
    )
    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return self._gm.success_response(serializer.data)
        except Http404:
            return self._gm.not_found(get_error_message("FAILED_TO_GET_KB"))
        except Exception as e:
            logger.exception(f"Error in fetching the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to retrieve knowledge base: {get_error_message('FAILED_TO_GET_KB')}"
            )

    @validated_request(
        operation_description="Update a knowledge base.",
        operation_summary="Update a knowledge base.",
        request_serializer=KnowledgeBaseSerializer,
        responses={200: KnowledgeBaseResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.validated_data)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return self._gm.success_response(serializer.data)
        except Http404:
            return self._gm.not_found(get_error_message("FAILED_TO_GET_KB"))
        except DRFValidationError as e:
            return self._gm.bad_request(e.detail)
        except Exception as e:
            logger.exception(f"Error in updating the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to update knowledge base: {get_error_message('FAILED_TO_UPDATE_KB')}"
            )

    @validated_request(
        operation_description="Partially update a knowledge base.",
        operation_summary="Partially update a knowledge base.",
        request_serializer=KnowledgeBaseSerializer,
        responses={200: KnowledgeBaseResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
        partial_request_validation=True,
    )
    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            instance = self.get_object()
            serializer = self.get_serializer(
                instance, data=request.validated_data, partial=True
            )
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return self._gm.success_response(serializer.data)
        except Http404:
            return self._gm.not_found(get_error_message("FAILED_TO_GET_KB"))
        except DRFValidationError as e:
            return self._gm.bad_request(e.detail)
        except Exception as e:
            logger.exception(f"Error in partially updating the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to update knowledge base: {get_error_message('FAILED_TO_UPDATE_KB')}"
            )

    @swagger_auto_schema(
        operation_description="Soft delete a knowledge base.",
        operation_summary="Soft delete a knowledge base.",
        responses={204: "No content", **MODEL_HUB_ERROR_RESPONSES},
    )
    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            instance = self.get_object()
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save()
            return self._gm.success_response(
                "Knowledge base soft deleted successfully", status=204
            )
        except Http404:
            return self._gm.not_found(get_error_message("FAILED_TO_GET_KB"))
        except Exception as e:
            logger.exception(f"Error in deleting the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to soft delete knowledge base: {get_error_message('FAILED_TO_DELETE_KB')}"
            )

    @swagger_auto_schema(
        operation_description="Get all supported embedding models.",
        operation_summary="Get supported embedding models.",
        responses={
            200: KnowledgeBaseEmbeddingModelsResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    @action(detail=False, methods=["get"])
    def supported_embedding_models(self, request):
        try:
            embedding_models = [
                {"value": choice[0], "label": choice[1]}
                for choice in KnowledgeBase.EmbeddingModelChoices.choices
            ]
            return self._gm.success_response(embedding_models)
        except Exception as e:
            logger.exception(f"Error in fetching the embeddings model: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to retrieve supported embedding models: {get_error_message('FAILED_TO_GET_EMBEDDINGS_MODEL')}"
            )
