"""Thin, workspace-scoped HTTP boundary for metric recipes and runs."""

import structlog
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, Throttled, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import ERROR_TYPE_BY_STATUS, ApiErrorType
from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.permissions.metrics import MetricWorkspacePermission
from tracer.serializers import derived_metric as contracts
from tracer.serializers.derived_metric import (
    DerivedMetricCommittedResultPageSerializer,
    DerivedMetricContributorPageSerializer,
    DerivedMetricContributorQuerySerializer,
    DerivedMetricEvaluatorPageSerializer,
    DerivedMetricEvaluatorQuerySerializer,
    DerivedMetricListQuerySerializer,
    DerivedMetricListSerializer,
    DerivedMetricRecomputeSerializer,
    DerivedMetricResultQuerySerializer,
    DerivedMetricRunRequestSerializer,
    DerivedMetricRunSerializer,
    DerivedMetricRunsSerializer,
    DerivedMetricSerializer,
    DerivedMetricValidationSerializer,
    DerivedMetricWriteSerializer,
)
from tracer.services.derived_metrics import reads, schema_install, service
from tracer.services.derived_metrics.contributors import read_contributors
from tracer.services.derived_metrics.cursor import newest_first_page
from tracer.services.derived_metrics.errors import MetricBusyError, MetricInputError
from tracer.services.derived_metrics.feature_flag import metrics_enabled
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
            deleted=False,
        )
        .select_related(
            "project", "project__metric_refresh", "workspace", "active_version"
        )
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


def _metric_error(code, message, *, status_code, retryable, **extra):
    """The common error envelope; ``code``, ``message`` and ``retryable`` stay as before."""
    return Response(
        {
            "type": ERROR_TYPE_BY_STATUS.get(status_code, ApiErrorType.API_ERROR).value,
            "code": code,
            "detail": message,
            "message": message,
            "retryable": retryable,
            **extra,
        },
        status=status_code,
    )


def _require_metric_schema():
    """Enabled without its ClickHouse schema, Metrics refuses every action with
    one stable code instead of reporting "recalculating" forever. The probe is
    a guard, not a new dependency: when it cannot reach ClickHouse the request
    goes on and its own storage reads report the failure."""
    try:
        schema_install.ensure_installed()
    except schema_install.MetricSchemaNotInstalled as exc:
        raise service.MetricUnavailableError(exc.code, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - a guard, never a new failure mode
        # Driver messages can carry connection details; the type is enough.
        structlog.get_logger(__name__).warning(
            "metric_schema_probe_failed", error_type=type(exc).__name__
        )


class MetricViewSet(viewsets.GenericViewSet):
    def initial(self, request, *args, **kwargs):
        # Hide every recipe/run action, including OPTIONS, before permissions,
        # validation, or storage work. Enabling leaves normal auth/RBAC intact.
        if not metrics_enabled(org=getattr(request, "organization", None)):
            raise Http404
        result = super().initial(request, *args, **kwargs)
        _require_metric_schema()
        return result

    def handle_exception(self, exc):
        if isinstance(exc, service.MetricUnavailableError):
            return _metric_error(
                exc.code,
                exc.message,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                retryable=True,
                **({"value_mode": exc.value_mode} if exc.value_mode else {}),
                **(
                    {"calculated_through": exc.calculated_through.isoformat()}
                    if exc.calculated_through
                    else {}
                ),
                **({"reason": exc.reason} if exc.reason else {}),
            )
        if isinstance(exc, service.SnapshotNotSupportedError):
            return _metric_error(
                "snapshot_not_supported",
                str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
                retryable=False,
            )
        if isinstance(exc, service.PreviewSupersededError):
            return _metric_error(
                "preview_superseded",
                str(exc),
                status_code=status.HTTP_409_CONFLICT,
                retryable=False,
            )
        if isinstance(exc, FormulaError):
            return super().handle_exception(ValidationError({"definition": str(exc)}))
        if isinstance(exc, MetricBusyError):
            return super().handle_exception(
                Throttled(wait=exc.retry_after, detail=str(exc.detail))
            )
        if isinstance(exc, MetricInputError):
            return super().handle_exception(ValidationError(exc.detail))
        if isinstance(exc, APIException | Http404 | DjangoPermissionDenied):
            return super().handle_exception(exc)
        structlog.get_logger(__name__).exception("derived_metric_api_unavailable")
        return _metric_error(
            "metric_unavailable",
            "Metric data is temporarily unavailable. Retry or reduce the window.",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            retryable=True,
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
        queryset = self.get_queryset()
        if params.get("project_id"):
            get_object_or_404(
                project_queryset_for_request(request), pk=params["project_id"]
            )
            queryset = queryset.filter(project_id=params["project_id"])
        page, next_cursor = newest_first_page(
            queryset, params.get("before"), params["limit"]
        )
        return Response(
            {
                "results": DerivedMetricSerializer(page, many=True).data,
                "next_cursor": next_cursor,
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
        query_serializer=DerivedMetricEvaluatorQuerySerializer,
        responses={200: DerivedMetricEvaluatorPageSerializer},
    )
    @action(detail=False, methods=["get"], url_path="evaluators")
    def evaluators(self, request):
        params = request.validated_query_data
        project = get_object_or_404(
            project_queryset_for_request(request), pk=params["project_id"]
        )
        return Response(
            DerivedMetricEvaluatorPageSerializer(
                service.list_evaluators(project, params["search"], params["limit"])
            ).data
        )

    @validated_request(
        query_serializer=DerivedMetricListQuerySerializer,
        responses={200: DerivedMetricRunsSerializer},
    )
    @action(detail=True, methods=["get"])
    def runs(self, request, pk=None):
        metric = self.get_object()
        params = request.validated_query_data
        queryset = DerivedMetricRun.objects.filter(
            definition__metric=metric
        ).select_related("definition")
        page, next_cursor = newest_first_page(
            queryset, params.get("before"), params["limit"]
        )
        return Response(
            {
                "results": DerivedMetricRunSerializer(page, many=True).data,
                "next_cursor": next_cursor,
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
        request_serializer=contracts.MetricPreviewRequestSerializer,
        responses={
            200: contracts.MetricPreviewSerializer,
            503: contracts.MetricUnavailableSerializer,
            409: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        metric = self.get_object()
        data = request.validated_data
        definition = get_object_or_404(
            DerivedMetricVersion,
            metric=metric,
            version=data.get(
                "version",
                metric.active_version.version
                if metric.active_version
                else metric.current_version,
            ),
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
            actor_id=request.user.id,
            request_id=data.get("request_id"),
            include_samples=data["include_samples"],
        )
        return Response(result)

    @validated_request(
        request_serializer=contracts.MetricDraftPreviewRequestSerializer,
        responses={
            200: contracts.MetricPreviewSerializer,
            503: contracts.MetricUnavailableSerializer,
            409: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=False, methods=["post"], url_path="preview")
    def preview_draft(self, request):
        from types import SimpleNamespace

        from tracer.models.eval_task import EvalTask
        from tracer.services.derived_metrics.spec import normalize_spec

        data = request.validated_data
        project = get_object_or_404(
            project_queryset_for_request(request), pk=data["project_id"]
        )
        service.validate_definition(data["definition"], project)
        definition = SimpleNamespace(
            **normalize_spec(data["definition"]), metric_id=f"draft:{project.id}"
        )
        source_run = str(data.get("source_run_id") or "")
        if source_run:
            get_object_or_404(EvalTask, pk=source_run, project=project, deleted=False)
        return Response(
            service.preview(
                definition,
                Scope(project.organization_id, request.workspace.id, project.id),
                data["window_start"],
                data["window_end"],
                data["filters"],
                source_run,
                actor_id=request.user.id,
                request_id=data.get("request_id"),
                include_samples=data["include_samples"],
            )
        )

    @validated_request(
        request_serializer=contracts.MetricDraftPreviewCancelSerializer,
        responses={200: contracts.MetricPreviewCancelledSerializer},
    )
    @action(detail=False, methods=["post"], url_path="preview/cancel")
    def cancel_draft_preview(self, request):
        project = get_object_or_404(
            project_queryset_for_request(request),
            pk=request.validated_data["project_id"],
        )
        return Response(
            service.cancel_preview(
                request.user.id,
                project.organization_id,
                f"draft:{project.id}",
                request.validated_data["request_id"],
            )
        )

    @validated_request(
        query_serializer=contracts.MetricEmptySerializer,
        responses={200: contracts.MetricCapabilitiesSerializer},
    )
    @action(detail=False, methods=["get"])
    def capabilities(self, request):
        from tracer.services.derived_metrics.spec import source_capabilities

        return Response(source_capabilities())

    @validated_request(
        request_serializer=contracts.MetricPreviewCancelSerializer,
        responses={200: contracts.MetricPreviewCancelledSerializer},
    )
    @action(detail=True, methods=["post"], url_path="preview/cancel")
    def cancel_preview(self, request, pk=None):
        metric = self.get_object()
        return Response(
            service.cancel_preview(
                request.user.id,
                metric.organization_id,
                metric.id,
                request.validated_data["request_id"],
            )
        )

    @validated_request(
        query_serializer=contracts.MetricWindowSerializer,
        responses={
            200: contracts.MetricKpiSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["get"])
    def kpi(self, request, pk=None):
        return Response(reads.read_kpi(self.get_object(), request.validated_query_data))

    @validated_request(
        query_serializer=contracts.MetricTrendQuerySerializer,
        responses={
            200: contracts.MetricTrendSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["get"])
    def trend(self, request, pk=None):
        return Response(
            reads.read_trend(self.get_object(), request.validated_query_data)
        )

    @validated_request(
        query_serializer=contracts.MetricDriversQuerySerializer,
        responses={
            200: contracts.MetricDriversSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["get"])
    def drivers(self, request, pk=None):
        return Response(
            reads.read_units(
                self.get_object(), request.validated_query_data, drivers=True
            )
        )

    @validated_request(
        query_serializer=DerivedMetricListQuerySerializer,
        responses={
            200: contracts.MetricHistorySerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["get"])
    def history(self, request, pk=None):
        return Response(
            reads.read_history(self.get_object(), request.validated_query_data)
        )

    @validated_request(
        query_serializer=contracts.MetricFieldsQuerySerializer,
        responses={
            200: contracts.MetricFieldsSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["get"])
    def fields(self, request, pk=None):
        metric = self.get_object()
        if metric.active_version is None:
            raise service.MetricUnavailableError(
                message="No completed active metric definition is available."
            )
        from tracer.services.derived_metrics.predicates import (
            MetricFieldMetadata,
            MetricFilterError,
            metric_filter_type,
        )
        from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS

        reads.require_materialized(metric.active_version)
        metadata = MetricFieldMetadata.from_metric(metric)
        context = request.validated_query_data["context"]
        try:
            kind = metric_filter_type(metadata, context)
        except MetricFilterError as exc:
            raise ValidationError({"context": str(exc)}) from exc
        version = metric.active_version
        projection = context == "users" and version.level == "conversation"
        return Response(
            {
                "results": [
                    {
                        "id": metadata.identity,
                        "level": "user" if projection else version.level,
                        "kind": version.kind,
                        "value_type": kind,
                        "value_format": "percent"
                        if projection and version.kind == "share"
                        else (
                            "number"
                            if projection and version.kind == "count"
                            else version.value_format
                        ),
                        "unit": "total"
                        if projection and version.kind == "count"
                        else version.unit,
                        "value_labels": version.value_labels
                        if kind == "boolean"
                        else {},
                        "operators": sorted(FILTER_TYPE_ALLOWED_OPS[kind]),
                        "projection": projection,
                    }
                ]
            }
        )

    @validated_request(
        query_serializer=contracts.MetricUnitValuesRequestSerializer,
        read_post=True,
        responses={
            200: contracts.MetricDriversSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["get", "post"], url_path="unit-values")
    def unit_values(self, request, pk=None):
        return Response(
            reads.read_units(self.get_object(), request.validated_query_data)
        )

    @validated_request(
        request_serializer=contracts.MetricRefreshRequestSerializer,
        responses={
            202: contracts.MetricRefreshSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(detail=True, methods=["post"], url_path="refresh")
    def refresh_now(self, request, pk=None):
        metric = self.get_object()
        if not request.validated_data["confirm"]:
            raise ValidationError("Refresh was not confirmed.")
        return Response(service.admit_refresh(metric), status=202)

    @validated_request(
        request_serializer=contracts.MetricActivationRequestSerializer,
        responses={200: DerivedMetricSerializer},
    )
    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        metric = self.get_object()
        service.activate_definition(metric, request.validated_data)
        return Response(
            DerivedMetricSerializer(self.get_queryset().get(pk=metric.pk)).data
        )

    @validated_request(
        query_serializer=DerivedMetricListQuerySerializer,
        responses={200: DerivedMetricRunsSerializer},
    )
    @action(detail=True, methods=["get"])
    def snapshots(self, request, pk=None):
        metric = self.get_object()
        params = request.validated_query_data
        queryset = DerivedMetricRun.objects.filter(
            definition__metric=metric, kind="snapshot", deleted_at__isnull=True
        ).select_related("definition")
        page, cursor = newest_first_page(
            queryset, params.get("before"), params["limit"]
        )
        return Response(
            {
                "results": DerivedMetricRunSerializer(page, many=True).data,
                "next_cursor": cursor,
            }
        )

    @validated_request(
        request_serializer=contracts.MetricSnapshotRequestSerializer,
        responses={
            202: DerivedMetricRunSerializer,
            400: contracts.MetricUnavailableSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @snapshots.mapping.post
    def create_snapshot(self, request, pk=None):
        return Response(
            service.request_snapshot(
                self.get_object(), request.validated_data, request.user
            ),
            status=202,
        )

    @validated_request(
        query_serializer=contracts.MetricEmptySerializer,
        responses={
            202: contracts.MetricSnapshotDeleteSerializer,
            503: contracts.MetricUnavailableSerializer,
        },
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path=r"snapshots/(?P<snapshot_id>[0-9a-f-]{36})",
    )
    def delete_snapshot(self, request, pk=None, snapshot_id=None):
        return Response(
            service.delete_snapshot(self.get_object(), snapshot_id, request.user),
            status=202,
        )


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
        responses={200: DerivedMetricCommittedResultPageSerializer},
    )
    @action(detail=True, methods=["get"])
    def results(self, request, pk=None):
        data = request.validated_query_data
        return Response(service.read_results(self.get_object(), **data))

    @validated_request(
        query_serializer=DerivedMetricContributorQuerySerializer,
        responses={200: DerivedMetricContributorPageSerializer},
    )
    @action(detail=True, methods=["get"])
    def contributors(self, request, pk=None):
        data = request.validated_query_data
        return Response(read_contributors(self.get_object(), **data))

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
