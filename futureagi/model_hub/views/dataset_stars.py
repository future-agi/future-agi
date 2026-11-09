"""Personal stars never grant access to a dataset."""

from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.dataset_stars import DatasetStar
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.serializers.develop_dataset_contracts import (
    DatasetStarRequestSerializer,
    DatasetStarResponseSerializer,
)
from model_hub.views.develop_dataset import _request_dataset_queryset
from tfc.middleware.workspace_context import get_current_workspace
from tfc.utils.api_contracts import validated_request


class DatasetStarView(APIView):
    permission_classes = [IsAuthenticated]
    # Stars are personal preferences, not shared workspace writes. Dataset
    # visibility and workspace membership are still checked below and in auth.
    workspace_write_exempt = True

    def initial(self, request, *args, **kwargs):
        # Before super(): DRF authenticates before checking permissions, and
        # malformed bodies must not reach the validated_request decorators.
        if getattr(settings, "DSX_DATASET_STARS_ENABLED", False) is not True:
            raise NotFound()
        return super().initial(request, *args, **kwargs)

    def _dataset(self, request, dataset_id):
        if not (getattr(request, "workspace", None) or get_current_workspace()):
            raise PermissionDenied("An authorized workspace is required.")
        return get_object_or_404(_request_dataset_queryset(request), pk=dataset_id)

    @validated_request(
        request_serializer=DatasetStarRequestSerializer,
        responses={200: DatasetStarResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, dataset_id):
        dataset = self._dataset(request, dataset_id)
        DatasetStar.objects.get_or_create(dataset=dataset, user=request.user)
        return Response(
            {
                "status": True,
                "result": {
                    "dataset_id": str(dataset.id),
                    "starred": True,
                },
            }
        )

    @validated_request(
        request_serializer=DatasetStarRequestSerializer,
        responses={200: DatasetStarResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def delete(self, request, dataset_id):
        dataset = self._dataset(request, dataset_id)
        DatasetStar.objects.filter(dataset=dataset, user=request.user).delete()
        return Response(
            {
                "status": True,
                "result": {
                    "dataset_id": str(dataset.id),
                    "starred": False,
                },
            }
        )
