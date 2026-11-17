from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tracer.models.bulk_export import BulkExportJob
from tracer.serializers.bulk_export import BulkExportCreateSerializer, BulkExportStatusSerializer
from tracer.services.bulk_export import JOB_TIMEOUT_SECONDS, expire_queued_export, fail_export
from tracer.services.export_destination import ExportConfigurationError, get_export_destination
from tracer.tasks.bulk_export import process_bulk_export
from tracer.utils.workspace_scope import project_queryset_for_request


ERROR_RESPONSES = {code: ApiErrorResponseSerializer for code in (400, 401, 403, 404)}


def _status(job, *, include_download=False):
    payload = dict(BulkExportStatusSerializer(job).data)
    payload["download_url"] = None
    if include_download and job.status == "completed":
        try:
            payload["download_url"] = get_export_destination(job).url(job.file_location)
        except ExportConfigurationError as exc:
            payload["error"] = str(exc)
        except Exception:
            # A removed encryption key/unavailable customer endpoint must not
            # leak credentials or vendor diagnostics from a status response.
            pass
    return payload


def _enqueue(job_id):
    try:
        process_bulk_export.delay(job_id)
    except Exception:
        fail_export(job_id, "Export could not be queued. Please retry.")


class BulkExportCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=BulkExportCreateSerializer,
        responses={202: BulkExportStatusSerializer, **ERROR_RESPONSES},
    )
    def post(self, request):
        data = request.validated_data
        project = get_object_or_404(project_queryset_for_request(request), pk=data["project_id"])
        with transaction.atomic():
            job = BulkExportJob.objects.create(
                project=project, created_by=request.user, row_type=data["row_type"],
                filters=data["filters"], format=data["format"],
            )
            transaction.on_commit(lambda: _enqueue(str(job.id)))
        job.refresh_from_db()
        return Response(_status(job), status=202)


class BulkExportDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: BulkExportStatusSerializer, **ERROR_RESPONSES})
    def get(self, request, job_id):
        job = get_object_or_404(
            BulkExportJob.objects.filter(project__in=project_queryset_for_request(request)),
            pk=job_id,
        )
        # Activity timeouts/process termination cannot execute a Python finally.
        # Bound both worker execution and the queue wait for one-shot jobs.
        if job.status == "queued" and job.schedule_id is None:
            expire_queued_export(job.id)
            job.refresh_from_db()
        if job.status == "running" and job.started_at and timezone.now() - job.started_at > timedelta(seconds=JOB_TIMEOUT_SECONDS + 300):
            fail_export(job.id, "Export timed out. Please retry.")
            job.refresh_from_db()
        return Response(_status(job, include_download=True))
