"""Create and inspect immutable dataset versions and source provenance."""

from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dataset_versions import (
    DatasetVersionCreateResponseSerializer,
    DatasetVersionCreateSerializer,
    DatasetVersionDetailResponseSerializer,
    DatasetVersionItemReadSerializer,
    DatasetVersionListResponseSerializer,
    DatasetVersionPageQuerySerializer,
    DatasetVersionReadSerializer,
)
from model_hub.services.dataset_version_service import (
    DatasetVersionItemInput,
    create_dataset_version,
)
from model_hub.utils.json_parser import BoundedJSONParser
from model_hub.utils.workspace_scope import scoped_dataset_queryset
from tfc.routers import primary_required
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()
_MAX_SNAPSHOT_BODY_BYTES = 10 * 1024 * 1024


class BoundedSnapshotJSONParser(BoundedJSONParser):
    max_body_bytes = _MAX_SNAPSHOT_BODY_BYTES


def _scoped_version_queryset(request, dataset_id):
    """Return non-deleted versions for a dataset visible to this tenant."""

    if getattr(request, "organization", None) is None:
        return DatasetVersion.objects.none()
    return DatasetVersion.objects.filter(
        dataset__in=scoped_dataset_queryset(request).filter(id=dataset_id),
        deleted=False,
    )


class DatasetVersionListView(APIView):
    """List immutable versions for one visible dataset."""

    permission_classes = [IsAuthenticated]
    parser_classes = (BoundedSnapshotJSONParser,)

    @validated_request(
        request_serializer=DatasetVersionCreateSerializer,
        responses={
            201: DatasetVersionCreateResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
        operation_id="dataset_versions_create",
        operation_description=(
            "Create an immutable dataset snapshot from an explicit items list "
            "of at most 10,000 entries and a JSON request body at most 10 MiB "
            "(10,485,760 bytes). Oversized bodies return 400 before JSON parsing. "
            "An empty list creates an empty version; "
            "mutable dataset rows are never read implicitly. Requires workspace "
            "write access. Parent versions must belong to the same visible "
            "dataset. Returns version metadata; read items through the detail "
            "endpoint. This POST is not idempotent: every accepted request, "
            "including a retry, creates a fresh version."
        ),
    )
    @primary_required
    def post(self, request: Request, dataset_id: UUID) -> Response:
        if getattr(request, "organization", None) is None:
            return _gm.not_found("Dataset not found")
        dataset = scoped_dataset_queryset(request).filter(id=dataset_id).first()
        if dataset is None:
            return _gm.not_found("Dataset not found")

        data = request.validated_data
        parent_id = data.get("parent_version_id")
        parent = None
        if parent_id is not None:
            parent = DatasetVersion.no_workspace_objects.filter(
                id=parent_id,
                dataset=dataset,
                deleted=False,
            ).first()
            if parent is None:
                return _gm.not_found("Parent dataset version not found")
        items = [DatasetVersionItemInput(**item) for item in data["items"]]
        try:
            version = create_dataset_version(
                dataset=dataset,
                items=items,
                created_by=request.user,
                parent_version=parent,
                description=data.get("description", ""),
                metadata=data.get("metadata"),
                source_revision=data.get("source_revision", ""),
            )
        except ValidationError as exc:
            return _gm.bad_request(
                exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            )
        version.item_count = len(items)
        return _gm.success_response(
            DatasetVersionReadSerializer(version).data,
            status=201,
        )

    @validated_request(
        query_serializer=DatasetVersionPageQuerySerializer,
        responses={
            200: DatasetVersionListResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    def get(self, request, dataset_id):
        if getattr(request, "organization", None) is None:
            return _gm.not_found("Dataset not found")
        dataset = scoped_dataset_queryset(request).filter(id=dataset_id).first()
        if dataset is None:
            return _gm.not_found("Dataset not found")

        versions = (
            _scoped_version_queryset(request, dataset.id)
            .annotate(
                item_count=Count(
                    "items",
                    filter=Q(items__deleted=False),
                    distinct=True,
                )
            )
            .order_by("-created_at", "-id")
        )
        limit = request.validated_query_data["limit"]
        offset = request.validated_query_data["offset"]
        page = list(versions[offset : offset + limit + 1])
        return _gm.success_response(
            {
                "dataset_id": dataset.id,
                "versions": DatasetVersionReadSerializer(page[:limit], many=True).data,
                "limit": limit,
                "offset": offset,
                "has_more": len(page) > limit,
            }
        )


class DatasetVersionDetailView(APIView):
    """Read one version and a bounded page of its provenance-bearing items."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DatasetVersionPageQuerySerializer,
        responses={
            200: DatasetVersionDetailResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    def get(self, request, dataset_id, version_id):
        version = (
            _scoped_version_queryset(request, dataset_id)
            .annotate(
                item_count=Count(
                    "items",
                    filter=Q(items__deleted=False),
                    distinct=True,
                )
            )
            .filter(id=version_id)
            .first()
        )
        if version is None:
            return _gm.not_found("Dataset version not found")

        limit = request.validated_query_data["limit"]
        offset = request.validated_query_data["offset"]

        items = DatasetVersionItem.objects.filter(
            dataset_version=version,
            deleted=False,
        ).order_by("created_at", "id")
        page = list(items[offset : offset + limit + 1])
        has_more = len(page) > limit
        if has_more:
            page = page[:limit]

        return _gm.success_response(
            {
                "version": DatasetVersionReadSerializer(version).data,
                "items": DatasetVersionItemReadSerializer(page, many=True).data,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
            }
        )
