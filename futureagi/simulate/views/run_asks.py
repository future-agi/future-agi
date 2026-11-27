"""Questions asked about a run's calls, from the run page.

A person picks calls (the whole run, or the calls they ticked) and up to five
typed questions. ``check`` shows what the ask would read and whether it could
launch, and writes nothing. The launch runs the same check again and is refused
when what the person saw has changed. Reading an ask returns requested, the six
counts and one row per call and question. Nothing here calls a model: grading
runs in the ask's own queued job.

Every route goes through the run first (``_execution_for_request``), so a run of
another organisation or workspace is not found, and an ask is only ever read
through a run its selection names. The work is in ``simulate.services.run_asks``
(check, launch) and ``simulate.selectors.run_asks`` (reads).
"""

from __future__ import annotations

from typing import Any

import structlog
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import viewer_denied
from simulate.models.test_execution import TestExecution
from simulate.selectors.run_asks import (
    ask_rows,
    ask_summaries,
    ask_summary,
    asks_of_run,
)
from simulate.serializers.run_ask import (
    AskCheckRequestSerializer,
    AskCheckResponseSerializer,
    AskDetailResponseSerializer,
    AskLaunchRequestSerializer,
    AskLaunchResponseSerializer,
    AskListQuerySerializer,
    AskListResponseSerializer,
    AskSummarySerializer,
)
from simulate.services import ask_engine
from simulate.services.ask_selection import SelectionRefused
from simulate.services.run_asks import (
    LaunchKeyTaken,
    check_run_ask,
    earlier_launch,
    freeze_run_selection,
    launch_key_reused,
    launch_run_ask,
    question_definitions,
)
from simulate.views.run_results_v3 import _execution_for_request
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import ApiErrorWithDetailsResponseSerializer
from tfc.utils.pagination import ExtendedPageNumberPagination

logger = structlog.get_logger(__name__)


class _AskPages(ExtendedPageNumberPagination):
    """A run's asks, newest first, 20 to a page; older ones on the next pages."""

    page_size = 20
    page_size_query_param = None


# Refusals about what was sent; the rest are about the run's calls or the launch
# as things stand now, and say so with 409. Ticked calls over the limit are
# stopped by the request's shape (400); a whole run over it is the run as it
# stands (409, too_many_calls).
_BAD_REQUEST = {
    "questions",
    "question_invalid",
    "audio_unsupported",
    "empty",
    "bad_filter",
}
_ERRORS = {
    400: ApiErrorWithDetailsResponseSerializer,
    404: ApiErrorWithDetailsResponseSerializer,
    409: ApiErrorWithDetailsResponseSerializer,
}


def _refused(refusal: SelectionRefused) -> Response:
    code: int
    if refusal.code == "not_found":
        code = status.HTTP_404_NOT_FOUND
    elif refusal.code in _BAD_REQUEST:
        code = status.HTTP_400_BAD_REQUEST
    else:
        code = status.HTTP_409_CONFLICT
    facts = {
        key: value
        for key, value in refusal.details.items()
        if value is None or isinstance(value, int | float | str)
    }
    return Response(
        build_error_envelope(
            refusal.message, status_code=code, code=refusal.code, extra=facts
        ),
        status=code,
    )


def _organization(request):
    return getattr(request, "organization", None) or request.user.organization


def _workspace_id(request, execution: TestExecution) -> Any:
    """The workspace the ask is kept in: the request's, else the run's own."""
    workspace = getattr(request, "workspace", None)
    if workspace is not None:
        return workspace.id
    return execution.run_test.workspace_id


def _freeze(request, execution: TestExecution, selection: dict):
    return freeze_run_selection(
        execution,
        selection,
        organization_id=_organization(request).id,
        workspace_id=_workspace_id(request, execution),
        asked_by_id=request.user.id,
    )


def _asks_of(request, execution: TestExecution):
    return asks_of_run(
        execution,
        organization=_organization(request),
        workspace_id=_workspace_id(request, execution),
    )


class RunAskCheckView(APIView):
    """POST: what an ask over these calls would read, and whether it could launch."""

    permission_classes = [IsAuthenticated]

    @viewer_denied("check a question about calls")
    @validated_request(
        request_serializer=AskCheckRequestSerializer,
        responses={200: AskCheckResponseSerializer, **_ERRORS},
        operation_id="simulate_v3_test_execution_ask_check",
        reject_unknown_fields=True,
    )
    def post(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        data = request.validated_data
        try:
            checked = check_run_ask(
                _freeze(request, execution, data["selection"]),
                question_definitions(data["questions"]),
                organization=_organization(request),
            )
        except SelectionRefused as refusal:
            return _refused(refusal)
        return Response(AskCheckResponseSerializer(checked).data)


class RunAsksView(APIView):
    """GET: this run's asks, newest first. POST: launch an ask the person accepted."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=AskListQuerySerializer,
        responses={
            200: AskListResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
        operation_id="simulate_v3_test_execution_asks",
    )
    def get(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        paginator = _AskPages()
        page = paginator.paginate_queryset(
            _asks_of(request, execution).order_by("-created_at", "-id"),
            request,
            view=self,
        )
        return paginator.get_paginated_response(
            AskSummarySerializer(ask_summaries(page or []), many=True).data
        )

    @viewer_denied("ask a question about calls")
    @validated_request(
        request_serializer=AskLaunchRequestSerializer,
        responses={
            200: AskLaunchResponseSerializer,
            201: AskLaunchResponseSerializer,
            **_ERRORS,
        },
        operation_id="simulate_v3_test_execution_ask_launch",
        reject_unknown_fields=True,
    )
    def post(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        data = request.validated_data
        launch_key = data["launch_key"]
        try:
            definitions = question_definitions(data["questions"])
            # A repeat of a launch that went through returns its ask, before
            # anything is read again or a gate could refuse what it already spent.
            earlier = earlier_launch(
                _asks_of(request, execution),
                launch_key,
                definitions,
                data["selection"],
            )
            if earlier is not None:
                return self._launched(earlier, status.HTTP_200_OK)
            selection = _freeze(request, execution, data["selection"])
            try:
                ask = launch_run_ask(
                    selection,
                    definitions,
                    accepted=data["accepted"],
                    organization=_organization(request),
                    asked_by=request.user,
                    launch_key=launch_key,
                )
            except LaunchKeyTaken:
                # The same launch, sent twice at once, saved first elsewhere.
                earlier = earlier_launch(
                    _asks_of(request, execution),
                    launch_key,
                    definitions,
                    data["selection"],
                )
                if earlier is None:
                    raise launch_key_reused() from None
                return self._launched(earlier, status.HTTP_200_OK)
        except SelectionRefused as refusal:
            return _refused(refusal)
        logger.info(
            "simulate_ask_launched",
            ask_id=str(ask.id),
            test_execution_id=str(execution.id),
            requested=len(selection.call_ids),
            questions=len(definitions),
        )
        return self._launched(ask, status.HTTP_201_CREATED)

    @staticmethod
    def _launched(ask, code: int) -> Response:
        return Response(
            AskLaunchResponseSerializer({"id": ask.id, "status": ask.status}).data,
            status=code,
        )


class RunAskDetailView(APIView):
    """GET: one ask, its counts and a row per call and question."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: AskDetailResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
        operation_id="simulate_v3_test_execution_ask_detail",
    )
    def get(self, request, test_execution_id, ask_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        ask = _asks_of(request, execution).filter(id=ask_id).first()
        if ask is None:
            code = status.HTTP_404_NOT_FOUND
            return Response(
                build_error_envelope("Ask not found", status_code=code), status=code
            )
        # Close what no worker will finish, for an ask whose job died.
        ask_engine.settle_ask(ask.id)
        ask.refresh_from_db()
        return Response(
            AskDetailResponseSerializer(
                {**ask_summary(ask), "results": ask_rows(ask)}
            ).data
        )
