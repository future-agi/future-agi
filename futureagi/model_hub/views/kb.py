from datetime import timedelta
from typing import Any

import structlog
from django.db.models import Q
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import filters, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

logger = structlog.get_logger(__name__)
from model_hub.models.kb import KnowledgeBase
from model_hub.serializers.kb import (
    KnowledgeBaseCreateSerializer,
    KnowledgeBaseSerializer,
)
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination


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

    @swagger_auto_schema(
        operation_description="Create a new knowledge base.",
        operation_summary="Create a new knowledge base.",
        responses={201: openapi.Response(description="Created knowledge base data")},
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        from tfc.ee_gating import EEFeature, check_ee_feature

        org = getattr(request, "organization", None) or request.user.organization
        check_ee_feature(EEFeature.KNOWLEDGE_BASE, org_id=str(org.id))

        try:
            serializer = self.get_serializer(data=request.data)
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
        responses={200: openapi.Response(description="List of knowledge bases")},
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
        responses={200: openapi.Response(description="Retrieved knowledge base data")},
    )
    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            response: Response = super().retrieve(request, *args, **kwargs)
            return self._gm.success_response(response.data)
        except Exception as e:
            logger.exception(f"Error in fetching the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to retrieve knowledge base: {get_error_message('FAILED_TO_GET_KB')}"
            )

    @swagger_auto_schema(
        operation_description="Update a knowledge base.",
        operation_summary="Update a knowledge base.",
        responses={200: openapi.Response(description="Updated knowledge base data")},
    )
    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        try:
            response: Response = super().update(request, *args, **kwargs)
            return self._gm.success_response(response.data)
        except Exception as e:
            logger.exception(f"Error in updating the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to update knowledge base: {get_error_message('FAILED_TO_UPDATE_KB')}"
            )

    @swagger_auto_schema(
        operation_description="Soft delete a knowledge base.",
        operation_summary="Soft delete a knowledge base.",
        responses={204: openapi.Response(description="No content")},
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
        except Exception as e:
            logger.exception(f"Error in deleting the knowledge base: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Failed to soft delete knowledge base: {get_error_message('FAILED_TO_DELETE_KB')}"
            )

    @swagger_auto_schema(
        operation_description="Get all supported embedding models.",
        operation_summary="Get supported embedding models.",
        responses={
            200: openapi.Response(description="List of supported embedding models")
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

    @swagger_auto_schema(
        operation_description="Generate a signed URL to download a KB file.",
        operation_summary="Download a KB file.",
        manual_parameters=[
            openapi.Parameter(
                "file_id", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True
            ),
            openapi.Parameter(
                "file_name", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True
            ),
        ],
        responses={200: openapi.Response(description="Signed download URL")},
    )
    @action(detail=True, methods=["get"], url_path="download-file")
    def download_file(self, request: Request, pk: str = None) -> Response:
        """Generate a short-lived signed URL for downloading a KB file."""
        try:
            kb = self.get_object()
            file_id = request.query_params.get("file_id")
            file_name = request.query_params.get("file_name")

            if not file_id or not file_name:
                return self._gm.bad_request_response(
                    "file_id and file_name are required."
                )

            from model_hub.utils.kb_indexer import KBIndexer

            indexer = KBIndexer()
            object_key = f"knowledge-base/{kb.id}/{file_id}"

            signed_url = indexer.minio_client.presigned_get_object(
                indexer.bucket_name,
                object_key,
                expires=timedelta(minutes=15),
            )
            return self._gm.success_response({"download_url": signed_url})
        except Exception as e:
            logger.exception(f"Error generating download URL: {str(e)}")
            return self._gm.internal_server_error_response(
                "Failed to generate download URL."
            )
