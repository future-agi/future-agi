from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from model_hub.models.dataset_eval_config import DatasetEvalConfig
from model_hub.serializers.dataset_eval_config import (
    DatasetEvalConfigCreateSerializer,
    DatasetEvalConfigSerializer,
)

_gm = GeneralMethods()


class DatasetEvalConfigViewSet(BaseModelViewSetMixin, ModelViewSet):
    """CRUD for auto-eval configs on datasets."""

    permission_classes = [IsAuthenticated]
    serializer_class = DatasetEvalConfigSerializer

    def get_queryset(self):
        qs = DatasetEvalConfig.objects.filter(
            organization=self.request.organization,
            deleted=False,
        ).select_related("eval_template")

        dataset_id = self.request.query_params.get("dataset_id")
        if dataset_id:
            qs = qs.filter(dataset_id=dataset_id)
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        return _gm.success_response(
            DatasetEvalConfigSerializer(qs, many=True).data
        )

    def retrieve(self, request, *args, **kwargs):
        return _gm.success_response(
            DatasetEvalConfigSerializer(self.get_object()).data
        )

    def create(self, request, *args, **kwargs):
        serializer = DatasetEvalConfigCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        config = serializer.save(
            organization=request.organization,
            workspace=getattr(request, "workspace", None),
            created_by=request.user,
        )
        return _gm.success_response(
            DatasetEvalConfigSerializer(config).data,
            status_code=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        config = self.get_object()
        allowed = {"enabled", "debounce_seconds", "max_concurrent",
                   "column_mapping", "filter_tags"}
        data = {k: v for k, v in request.data.items() if k in allowed}
        serializer = DatasetEvalConfigCreateSerializer(
            config, data=data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return _gm.success_response(DatasetEvalConfigSerializer(config).data)

    def destroy(self, request, *args, **kwargs):
        config = self.get_object()
        config.deleted = True
        config.save(update_fields=["deleted"])
        return _gm.success_response({"message": "Config deleted"})
