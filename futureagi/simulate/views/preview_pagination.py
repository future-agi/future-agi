"""Read-only, deadline-bounded list APIs used by SimulationTestMode."""

from __future__ import annotations

import time
from contextlib import contextmanager

import structlog
from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from simulate.models import CallExecution, RunTest, TestExecution
from simulate.serializers.preview_pagination import (
    SimulationCallPreviewCursorQuerySerializer,
    SimulationPreviewCursorQuerySerializer,
    SimulationPreviewErrorSerializer,
    SimulationPreviewPageSerializer,
)
from simulate.services.preview_pagination import (
    PreviewCursorInvalid,
    PreviewSnapshotChanged,
    PreviewSnapshotUnavailable,
    paginate_preview_snapshot,
)
from simulate.views.scoping import run_test_workspace_filter
from tfc.utils.api_contracts import validated_request

logger = structlog.get_logger(__name__)

PREVIEW_SERVER_WALL_SECONDS = settings.INTERACTIVE_READ_DEFAULT_WALL_MS / 1_000
SIMULATION_PREVIEW_RESPONSES = {
    200: SimulationPreviewPageSerializer,
    400: SimulationPreviewErrorSerializer,
    404: SimulationPreviewErrorSerializer,
    409: SimulationPreviewErrorSerializer,
    503: SimulationPreviewErrorSerializer,
}


class PreviewReadDeadlineExceeded(TimeoutError):
    pass


def _remaining_ms(deadline: float) -> int:
    remaining = int((deadline - time.monotonic()) * 1_000)
    if remaining <= 0:
        raise PreviewReadDeadlineExceeded("Simulation preview read deadline exceeded.")
    return remaining


def _set_statement_timeout(deadline: float) -> None:
    if connection.vendor != "postgresql":
        raise PreviewSnapshotUnavailable(
            "Exact simulation preview pagination requires PostgreSQL."
        )
    timeout_ms = _remaining_ms(deadline)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('statement_timeout', %s, true)", [str(timeout_ms)]
        )


@contextmanager
def _repeatable_read_snapshot(deadline: float):
    """Run the page in one read-only repeatable PostgreSQL snapshot."""

    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
        _set_statement_timeout(deadline)
        with connection.cursor() as cursor:
            cursor.execute("SELECT txid_current_snapshot()::text, CURRENT_TIMESTAMP")
            snapshot, snapshot_at = cursor.fetchone()
        if not snapshot:
            raise PreviewSnapshotUnavailable(
                "The simulation preview snapshot could not be established."
            )
        yield str(snapshot), snapshot_at


def _error(code: str, detail: str, http_status: int, **extra) -> Response:
    return Response(
        {"code": code, "detail": detail, **extra},
        status=http_status,
    )


def _query_validation_error(_errors) -> Response:
    return _error(
        "simulation_preview_query_invalid",
        "Invalid simulation preview query parameters.",
        status.HTTP_400_BAD_REQUEST,
    )


class _SimulationPreviewBaseView(APIView):
    permission_classes = [IsAuthenticated]
    preview_kind: str

    def _read_page(self, request, parent_id: str) -> dict:
        raise NotImplementedError

    def _serve(self, request, *args, **kwargs):
        parent_id = str(kwargs.get("run_test_id") or kwargs.get("test_execution_id"))
        try:
            return Response(
                self._read_page(request, parent_id),
                status=status.HTTP_200_OK,
            )
        except PreviewCursorInvalid as exc:
            return _error(
                "simulation_preview_cursor_invalid",
                str(exc),
                status.HTTP_400_BAD_REQUEST,
                restart_required=True,
            )
        except PreviewSnapshotChanged as exc:
            return _error(
                "simulation_preview_snapshot_changed",
                str(exc),
                status.HTTP_409_CONFLICT,
                restart_required=True,
            )
        except Http404:
            if request.validated_query_data.get("cursor"):
                return _error(
                    "simulation_preview_snapshot_changed",
                    "Simulation preview source changed while more rows were loading.",
                    status.HTTP_409_CONFLICT,
                    restart_required=True,
                )
            return _error(
                "simulation_preview_not_found",
                "Simulation preview source was not found.",
                status.HTTP_404_NOT_FOUND,
            )
        except (
            PreviewReadDeadlineExceeded,
            PreviewSnapshotUnavailable,
            DatabaseError,
        ):
            logger.warning(
                "simulation_preview_read_unavailable",
                kind=self.preview_kind,
                parent_id=parent_id,
            )
            return _error(
                "simulation_preview_read_unavailable",
                "Simulation preview data could not be read within the deadline. Retry.",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class RunTestPreviewExecutionsView(_SimulationPreviewBaseView):
    preview_kind = "run_test_executions"

    @validated_request(
        query_serializer=SimulationPreviewCursorQuerySerializer,
        responses=SIMULATION_PREVIEW_RESPONSES,
        reject_unknown_fields=True,
        validation_error_response=_query_validation_error,
        strict_response_validation=True,
    )
    def get(self, request, *args, **kwargs):
        return self._serve(request, *args, **kwargs)

    def _read_page(self, request, parent_id: str) -> dict:
        deadline = time.monotonic() + PREVIEW_SERVER_WALL_SECONDS
        query = request.validated_query_data
        with _repeatable_read_snapshot(deadline) as (snapshot, transaction_now):
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            _set_statement_timeout(deadline)
            run_test = get_object_or_404(
                RunTest,
                run_test_workspace_filter(request),
                id=parent_id,
                organization=organization,
                deleted=False,
            )
            _set_statement_timeout(deadline)
            return paginate_preview_snapshot(
                TestExecution.objects.filter(run_test=run_test, deleted=False),
                kind=self.preview_kind,
                parent_id=parent_id,
                scope_id=None,
                page_size=query["page_size"],
                snapshot_at=transaction_now,
                snapshot=snapshot,
                cursor=query.get("cursor"),
                fields=("id", "status", "created_at"),
                before_query=lambda: _set_statement_timeout(deadline),
            )


class TestExecutionPreviewCallsView(_SimulationPreviewBaseView):
    preview_kind = "test_execution_calls"

    @validated_request(
        query_serializer=SimulationCallPreviewCursorQuerySerializer,
        responses=SIMULATION_PREVIEW_RESPONSES,
        reject_unknown_fields=True,
        validation_error_response=_query_validation_error,
        strict_response_validation=True,
    )
    def get(self, request, *args, **kwargs):
        return self._serve(request, *args, **kwargs)

    def _read_page(self, request, parent_id: str) -> dict:
        deadline = time.monotonic() + PREVIEW_SERVER_WALL_SECONDS
        query = request.validated_query_data
        run_test_id = str(query["run_test_id"])
        with _repeatable_read_snapshot(deadline) as (snapshot, transaction_now):
            organization = (
                getattr(request, "organization", None) or request.user.organization
            )
            _set_statement_timeout(deadline)
            test_execution = get_object_or_404(
                TestExecution.objects.select_related("run_test"),
                run_test_workspace_filter(request, "run_test"),
                id=parent_id,
                run_test_id=run_test_id,
                run_test__organization=organization,
                run_test__deleted=False,
                deleted=False,
            )
            _set_statement_timeout(deadline)
            return paginate_preview_snapshot(
                CallExecution.objects.filter(
                    test_execution=test_execution,
                    deleted=False,
                ),
                kind=self.preview_kind,
                parent_id=parent_id,
                scope_id=run_test_id,
                page_size=query["page_size"],
                snapshot_at=transaction_now,
                snapshot=snapshot,
                cursor=query.get("cursor"),
                fields=("id", "status", "created_at"),
                before_query=lambda: _set_statement_timeout(deadline),
            )
