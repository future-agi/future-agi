"""Tenant-scoped declared schema persistence independent of dataset versions."""

from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.serializers.dataset_schema import (
    DatasetSchemaResponseSerializer,
    DatasetSchemaWriteSerializer,
)
from model_hub.services.dataset_splits import scoped_dataset
from tfc.utils.api_contracts import validated_request


class DatasetSchemaView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: DatasetSchemaResponseSerializer})
    def get(self, request, dataset_id):
        dataset = scoped_dataset(request, dataset_id)
        stored = (dataset.dataset_config or {}).get("schema") or {}
        return Response(
            {
                "status": True,
                "result": {
                    "declared": stored.get("declared") or {},
                    "enforce": stored.get("enforce", "off"),
                    "preset": stored.get("preset", ""),
                },
            }
        )

    @validated_request(
        request_serializer=DatasetSchemaWriteSerializer,
        responses={200: DatasetSchemaResponseSerializer},
    )
    def put(self, request, dataset_id):
        with transaction.atomic():
            dataset = scoped_dataset(request, dataset_id, write=True, lock=True)
            dataset.dataset_config = {
                **(dataset.dataset_config or {}),
                "schema": dict(request.validated_data),
            }
            dataset.save(update_fields=["dataset_config", "updated_at"])
        return Response({"status": True, "result": dict(request.validated_data)})
