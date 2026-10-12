"""Read-only dataset-version identity and provenance endpoints.

The legacy ``Dataset -> Row -> Cell`` API remains unchanged.  These endpoints
only expose the small immutable projection introduced by the dataset-version
MVP, so callers can inspect stable example IDs and source trace/span links
before experiment runs start writing against the contract.
"""

from django.db.models import Count, Q
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.dataset_versions import (
    DatasetVersionDetailResponseSerializer,
    DatasetVersionItemReadSerializer,
    DatasetVersionListResponseSerializer,
    DatasetVersionPageQuerySerializer,
    DatasetVersionReadSerializer,
)
from model_hub.utils.workspace_scope import (
    request_organization,
    scoped_dataset_queryset,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


def _scoped_version_queryset(request, dataset_id):
    """Return non-deleted versions for a dataset visible to this tenant."""

    if request_organization(request) is None:
        return DatasetVersion.objects.none()
    return DatasetVersion.objects.filter(
        dataset__in=scoped_dataset_queryset(request).filter(id=dataset_id),
        deleted=False,
    )


class DatasetVersionListView(APIView):
    """List immutable versions for one visible dataset."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DatasetVersionPageQuerySerializer,
        responses={
            200: DatasetVersionListResponseSerializer,
            **MODEL_HUB_ERROR_RESPONSES,
        },
    )
    def get(self, request, dataset_id):
        if request_organization(request) is None:
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
