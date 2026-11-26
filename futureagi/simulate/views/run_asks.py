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
from simulate.selectors.run_asks import ask_rows, ask_summary, asks_of_run
from simulate.serializers.run_ask import (
    AskCheckRequestSerializer,
    AskCheckResponseSerializer,
    AskDetailResponseSerializer,
    AskLaunchRequestSerializer,
    AskLaunchResponseSerializer,
    AskListResponseSerializer,
)
from simulate.services import ask_engine
from simulate.services.ask_selection import SelectionRefused
from simulate.services.run_asks import (
    check_run_ask,
    freeze_run_selection,
    launch_run_ask,
    question_definitions,
)
from simulate.views.run_results_v3 import _execution_for_request
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import ApiErrorWithDetailsResponseSerializer

logger = structlog.get_logger(__name__)

# The asks a run's page lists, newest first.
LISTED_ASKS = 20

# Refusals about what was sent; the rest are about the run's calls or the launch
# as things stand now, and say so with 409.
_BAD_REQUEST = {
    "questions",
    "question_invalid",
    "audio_unsupported",
    "empty",
    "too_many",
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
        responses={
            200: AskListResponseSerializer,
            404: ApiErrorWithDetailsResponseSerializer,
        },
        operation_id="simulate_v3_test_execution_asks",
    )
    def get(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        asks = _asks_of(request, execution).order_by("-created_at", "-id")[:LISTED_ASKS]
        return Response(
            AskListResponseSerializer({"asks": [ask_summary(ask) for ask in asks]}).data
        )

    @viewer_denied("ask a question about calls")
    @validated_request(
        request_serializer=AskLaunchRequestSerializer,
        responses={201: AskLaunchResponseSerializer, **_ERRORS},
        operation_id="simulate_v3_test_execution_ask_launch",
        reject_unknown_fields=True,
    )
    def post(self, request, test_execution_id, *args, **kwargs):
        execution = _execution_for_request(request, test_execution_id)
        data = request.validated_data
        try:
            definitions = question_definitions(data["questions"])
            selection = _freeze(request, execution, data["selection"])
            ask = launch_run_ask(
                selection,
                definitions,
                accepted=data["accepted"],
                organization=_organization(request),
                asked_by=request.user,
            )
        except SelectionRefused as refusal:
            return _refused(refusal)
        logger.info(
            "simulate_ask_launched",
            ask_id=str(ask.id),
            test_execution_id=str(execution.id),
            requested=len(selection.call_ids),
            questions=len(definitions),
        )
        return Response(
            AskLaunchResponseSerializer({"id": ask.id, "status": ask.status}).data,
            status=status.HTTP_201_CREATED,
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
