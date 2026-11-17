from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tracer.models.export_schedule import ExportSchedule
from tracer.serializers.export_schedule import (
    ExportScheduleListQuerySerializer,
    ExportScheduleListSerializer,
    ExportSchedulePatchSerializer,
    ExportScheduleReadSerializer,
    ExportScheduleWriteSerializer,
)
from tracer.utils.workspace_scope import project_queryset_for_request


ERROR_RESPONSES = {code: ApiErrorResponseSerializer for code in (400, 401, 403, 404)}


def _scoped_schedules(request):
    return ExportSchedule.objects.filter(project__in=project_queryset_for_request(request))


class ExportScheduleListView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=ExportScheduleListQuerySerializer,
        responses={200: ExportScheduleListSerializer, **ERROR_RESPONSES},
    )
    def get(self, request):
        project = get_object_or_404(
            project_queryset_for_request(request), pk=request.validated_query_data["project_id"],
        )
        schedules = _scoped_schedules(request).filter(project=project)
        return Response({"results": ExportScheduleReadSerializer(schedules, many=True).data})

    @validated_request(
        request_serializer=ExportScheduleWriteSerializer,
        responses={201: ExportScheduleReadSerializer, **ERROR_RESPONSES},
    )
    def post(self, request):
        get_object_or_404(project_queryset_for_request(request), pk=request.validated_data["project_id"])
        schedule = request.validated_serializer.save(created_by=request.user)
        return Response(ExportScheduleReadSerializer(schedule).data, status=201)


class ExportScheduleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: ExportScheduleReadSerializer, **ERROR_RESPONSES})
    def get(self, request, schedule_id):
        schedule = get_object_or_404(_scoped_schedules(request), pk=schedule_id)
        return Response(ExportScheduleReadSerializer(schedule).data)

    @validated_request(
        request_serializer=ExportSchedulePatchSerializer,
        partial_request_validation=True,
        responses={200: ExportScheduleReadSerializer, **ERROR_RESPONSES},
    )
    def patch(self, request, schedule_id):
        with transaction.atomic():
            schedule = get_object_or_404(_scoped_schedules(request).select_for_update(), pk=schedule_id)
            serializer = ExportScheduleWriteSerializer(schedule, data=request.validated_data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        return Response(ExportScheduleReadSerializer(schedule).data)

    @validated_request(responses={204: None, **ERROR_RESPONSES})
    def delete(self, request, schedule_id):
        with transaction.atomic():
            schedule = get_object_or_404(_scoped_schedules(request).select_for_update(), pk=schedule_id)
            schedule.delete()
        return Response(status=204)
