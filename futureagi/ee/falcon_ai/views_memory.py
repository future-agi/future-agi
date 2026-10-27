from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ee.falcon_ai.models import FalconMemory
from ee.falcon_ai.serializers_contracts import (
    FalconErrorResponseSerializer,
    FalconMemoryDetailResponseSerializer,
    FalconMemoryListResponseSerializer,
)
from ee.falcon_ai.serializers_memory import (
    FalconMemoryCreateSerializer,
    FalconMemorySerializer,
)
from tfc.utils.general_methods import GeneralMethods

_gm = GeneralMethods()


class MemoryListView(APIView):
    """List and create workspace memories."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: FalconMemoryListResponseSerializer,
            403: FalconErrorResponseSerializer,
        }
    )
    def get(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return _gm.forbidden_response("No organization context")

        memories = FalconMemory.objects.filter(
            organization=organization,
        )

        if workspace:
            memories = memories.filter(workspace=workspace)

        serializer = FalconMemorySerializer(memories, many=True)
        return Response({"status": True, "results": serializer.data})

    @swagger_auto_schema(
        request_body=FalconMemoryCreateSerializer,
        responses={
            200: FalconMemoryDetailResponseSerializer,
            201: FalconMemoryDetailResponseSerializer,
            403: FalconErrorResponseSerializer,
        },
    )
    def post(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization or not workspace:
            return _gm.forbidden_response("Organization and workspace context required")

        serializer = FalconMemoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        memory, created = FalconMemory.objects.update_or_create(
            workspace=workspace,
            key=data["key"],
            defaults={
                "value": data["value"],
                "source": "user",
                "organization": organization,
                "created_by": request.user,
            },
        )

        result_serializer = FalconMemorySerializer(memory)
        return Response(
            {"status": True, "result": result_serializer.data},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MemoryDeleteView(APIView):
    """Delete a workspace memory."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            204: "Memory deleted",
            403: FalconErrorResponseSerializer,
            404: FalconErrorResponseSerializer,
        }
    )
    def delete(self, request, memory_id):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)

        if not organization:
            return _gm.forbidden_response("No organization context")

        filters = {
            "id": memory_id,
            "organization": organization,
        }
        if workspace:
            filters["workspace"] = workspace

        try:
            memory = FalconMemory.objects.get(**filters)
        except FalconMemory.DoesNotExist:
            return _gm.not_found("Memory not found")

        memory.delete()
        return Response({"status": True}, status=status.HTTP_204_NO_CONTENT)
