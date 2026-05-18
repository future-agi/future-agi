from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import SimulatorAgent
from simulate.serializers.simulator_agent import (
    SimulatorAgentDeleteResponseSerializer,
    SimulatorAgentListResponseSerializer,
    SimulatorAgentSerializer,
    SimulatorAgentValidationErrorResponseSerializer,
)
from tfc.utils.api_serializers import ApiErrorWithDetailsResponseSerializer
from tfc.utils.pagination import ExtendedPageNumberPagination


class SimulatorAgentListView(APIView):
    """List simulator agents with pagination and search"""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: SimulatorAgentListResponseSerializer,
            400: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def get(self, request):
        # Get query parameters
        search_query = request.GET.get("search", "").strip()
        page_size = int(request.GET.get("limit", 10))

        # Base queryset - filter by organization
        queryset = SimulatorAgent.objects.filter(
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        )

        # Apply search filter if provided
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query)
                | Q(prompt__icontains=search_query)
                | Q(model__icontains=search_query)
                | Q(voice_provider__icontains=search_query)
            )

        # Order by created_at descending
        queryset = queryset.order_by("-created_at")

        # Apply pagination
        paginator = ExtendedPageNumberPagination()
        paginator.page_size = page_size
        paginated_queryset = paginator.paginate_queryset(queryset, request)

        # Serialize data
        serializer = SimulatorAgentSerializer(paginated_queryset, many=True)

        # Return paginated response
        return paginator.get_paginated_response(serializer.data)


class CreateSimulatorAgentView(APIView):
    """Create a new simulator agent"""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=SimulatorAgentSerializer,
        responses={
            201: SimulatorAgentSerializer,
            400: SimulatorAgentValidationErrorResponseSerializer,
        },
    )
    def post(self, request):
        serializer = SimulatorAgentSerializer(
            data=request.data, context={"request": request}
        )

        if serializer.is_valid():
            simulator_agent = serializer.save()
            return Response(
                SimulatorAgentSerializer(simulator_agent).data,
                status=status.HTTP_201_CREATED,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class SimulatorAgentDetailView(APIView):
    """Get details of a specific simulator agent"""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: SimulatorAgentSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def get(self, request, agent_id):
        simulator_agent = get_object_or_404(
            SimulatorAgent,
            id=agent_id,
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        )

        serializer = SimulatorAgentSerializer(simulator_agent)
        return Response(serializer.data)


class EditSimulatorAgentView(APIView):
    """Edit an existing simulator agent"""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        request_body=SimulatorAgentSerializer,
        responses={
            200: SimulatorAgentSerializer,
            400: SimulatorAgentValidationErrorResponseSerializer,
        },
    )
    def put(self, request, agent_id):
        simulator_agent = get_object_or_404(
            SimulatorAgent,
            id=agent_id,
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        )

        serializer = SimulatorAgentSerializer(
            simulator_agent,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if serializer.is_valid():
            updated_agent = serializer.save()
            return Response(
                SimulatorAgentSerializer(updated_agent).data, status=status.HTTP_200_OK
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DeleteSimulatorAgentView(APIView):
    """Soft delete a simulator agent"""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        responses={
            200: SimulatorAgentDeleteResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
            500: ApiErrorWithDetailsResponseSerializer,
        }
    )
    def delete(self, request, agent_id):
        simulator_agent = get_object_or_404(
            SimulatorAgent,
            id=agent_id,
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        )

        # Perform soft delete
        simulator_agent.deleted = True
        simulator_agent.save()

        return Response(
            {"message": "Simulator agent deleted successfully"},
            status=status.HTTP_200_OK,
        )
