"""Thin, workspace-scoped HTTP boundary for metric recipes and runs."""

import structlog
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.permissions.metrics import MetricWorkspacePermission
from tracer.serializers.derived_metric import (
    DerivedMetricListQuerySerializer,
    DerivedMetricListSerializer,
    DerivedMetricRecomputeSerializer,
    DerivedMetricResultPageSerializer,
    DerivedMetricResultQuerySerializer,
    DerivedMetricRunRequestSerializer,
    DerivedMetricRunSerializer,
    DerivedMetricRunsSerializer,
    DerivedMetricSerializer,
    DerivedMetricValidationSerializer,
    DerivedMetricWriteSerializer,
)
from tracer.services.derived_metrics import service
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.formula import FormulaError
from tracer.services.derived_metrics.queries import Scope
from tracer.utils.workspace_scope import (
    get_request_organization,
    project_queryset_for_request,
)


def metric_queryset(request):
    return (
        DerivedMetric.objects.filter(
            workspace=request.workspace,
            organization=get_request_organization(request),
            project__in=project_queryset_for_request(request),
        )
        .select_related("project", "workspace")
        .prefetch_related(
            Prefetch(
                "versions",
                queryset=DerivedMetricVersion.objects.filter(
                    version=F("metric__current_version")
                ),
            )
        )
    )


def _enqueue(run):
    from tracer.tasks.derived_metric import compute_derived_metric

    def enqueue():
        try:
            compute_derived_metric.delay(str(run.id))
        except Exception:
            DerivedMetricRun.objects.filter(pk=run.id).update(
                status=DerivedMetricRun.Status.FAILED,
                error="Computation could not be queued. Retry this run.",
            )
            raise

    transaction.on_commit(enqueue)


class MetricViewSet(viewsets.GenericViewSet):
    def handle_exception(self, exc):
        if isinstance(exc, MetricInputError):
            return super().handle_exception(ValidationError(exc.detail))
        if isinstance(exc, (APIException, Http404, DjangoPermissionDenied)):
            return super().handle_exception(exc)
        structlog.get_logger(__name__).exception("derived_metric_api_unavailable")
        return GeneralMethods().custom_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Metric data is temporarily unavailable. Retry or reduce the window.",
            code="metric_unavailable",
        )


class DerivedMetricViewSet(MetricViewSet):
    permission_classes = [IsAuthenticated, MetricWorkspacePermission]
    serializer_class = DerivedMetricSerializer
    pagination_class = None
    lookup_value_regex = "[0-9a-f-]{36}"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DerivedMetric.objects.none()
        return metric_queryset(self.request)

    @validated_request(
        query_serializer=DerivedMetricListQuerySerializer,
        responses={200: DerivedMetricListSerializer},
    )
    def list(self, request):
        params = request.validated_query_data
        queryset = self.get_queryset().order_by("-id")
        if params.get("project_id"):
            queryset = queryset.filter(project_id=params["project_id"])
        if params.get("before"):
            queryset = queryset.filter(id__lt=params["before"])
        rows = list(queryset[: params["limit"] + 1])
        return Response(
            {
                "results": DerivedMetricSerializer(
                    rows[: params["limit"]], many=True
                ).data,
                "next_cursor": str(rows[params["limit"] - 1].id)
                if len(rows) > params["limit"]
                else None,
            }
        )

    @validated_request(responses={200: DerivedMetricSerializer})
    def retrieve(self, request, pk=None):
        return Response(DerivedMetricSerializer(self.get_object()).data)

    def _save(self, request, metric=None):
        data = request.validated_data
        project = get_object_or_404(
            project_queryset_for_request(request), pk=data["project_id"]
        )
        try:
            result = service.save_definition(
                data, project, request.workspace, request.user, metric
            )
        except FormulaError as exc:
            raise ValidationError({"formula": str(exc)}) from exc
        except IntegrityError as exc:
            raise ValidationError(
                {"name": "A metric with this name already exists in the project."}
            ) from exc
        return Response(
            DerivedMetricSerializer(self.get_queryset().get(pk=result.id)).data,
            status=status.HTTP_200_OK if metric else status.HTTP_201_CREATED,
        )

    @validated_request(
        request_serializer=DerivedMetricWriteSerializer,
        responses={201: DerivedMetricSerializer},
    )
    def create(self, request):
        return self._save(request)

    @validated_request(
        request_serializer=DerivedMetricWriteSerializer,
        responses={200: DerivedMetricSerializer},
    )
    def update(self, request, pk=None):
        return self._save(request, self.get_object())

    @validated_request(
        request_serializer=DerivedMetricWriteSerializer,
        responses={200: DerivedMetricValidationSerializer},
    )
    @action(detail=False, methods=["post"], url_path="validate")
    def validate_formula(self, request):
        data = request.validated_data
        project = get_object_or_404(
            project_queryset_for_request(request), pk=data["project_id"]
        )
        try:
            result = service.validate_definition(data, project)
        except FormulaError as exc:
            raise ValidationError({"formula": str(exc)}) from exc
        return Response(result)

    @validated_request(
        query_serializer=DerivedMetricListQuerySerializer,
        responses={200: DerivedMetricRunsSerializer},
    )
    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        metric = self.get_object()
        params = request.validated_query_data
        queryset = (
            DerivedMetricRun.objects.filter(definition__metric=metric)
            .select_related("definition")
            .order_by("-id")
        )
        if params.get("before"):
            queryset = queryset.filter(id__lt=params["before"])
        rows = list(queryset[: params["limit"] + 1])
        return Response(
            {
                "results": DerivedMetricRunSerializer(
                    rows[: params["limit"]], many=True
                ).data,
                "next_cursor": str(rows[params["limit"] - 1].id)
                if len(rows) > params["limit"]
                else None,
            }
        )

    @validated_request(
        request_serializer=DerivedMetricRunRequestSerializer,
        responses={202: DerivedMetricRunSerializer},
    )
    @runs.mapping.post
    def start_run(self, request, pk=None):
        run, created = service.create_run(
            self.get_object(), request.validated_data, request.user
        )
        if created:
            _enqueue(run)
        return Response(
            DerivedMetricRunSerializer(run).data, status=status.HTTP_202_ACCEPTED
        )

    @validated_request(
        request_serializer=DerivedMetricRunRequestSerializer,
        responses={200: DerivedMetricResultPageSerializer},
    )
    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        metric = self.get_object()
        data = request.validated_data
        definition = get_object_or_404(
            DerivedMetricVersion,
            metric=metric,
            version=data.get("version", metric.current_version),
        )
        # Validate source-run ownership through the same project boundary as runs.
        source_run = str(data.get("source_run_id") or "")
        if source_run:
            from tracer.models.eval_task import EvalTask

            get_object_or_404(EvalTask, pk=source_run, project_id=metric.project_id)
        result = service.preview(
            definition,
            Scope(metric.organization_id, metric.workspace_id, metric.project_id),
            data["window_start"],
            data["window_end"],
            data["filters"],
            source_run,
        )
        return Response(result)


class DerivedMetricRunViewSet(MetricViewSet):
    permission_classes = [IsAuthenticated, MetricWorkspacePermission]
    serializer_class = DerivedMetricRunSerializer
    pagination_class = None
    lookup_value_regex = "[0-9a-f-]{36}"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return DerivedMetricRun.objects.none()
        return DerivedMetricRun.objects.filter(
            definition__metric__in=metric_queryset(self.request)
        ).select_related("definition__metric__project", "definition__metric__workspace")

    @validated_request(responses={200: DerivedMetricRunSerializer})
    def retrieve(self, request, pk=None):
        return Response(DerivedMetricRunSerializer(self.get_object()).data)

    @validated_request(
        query_serializer=DerivedMetricResultQuerySerializer,
        responses={200: DerivedMetricResultPageSerializer},
    )
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        data = request.validated_query_data
        return Response(service.read_results(self.get_object(), **data))

    @validated_request(
        request_serializer=DerivedMetricRecomputeSerializer,
        responses={202: DerivedMetricRunSerializer},
    )
    @action(detail=True, methods=["post"])
    def recompute(self, request, pk=None):
        if not request.validated_data["confirm"]:
            raise ValidationError("Recompute was not confirmed.")
        run = service.queue_recompute(self.get_object(), request.user)
        _enqueue(run)
        return Response(
            DerivedMetricRunSerializer(run).data, status=status.HTTP_202_ACCEPTED
        )
