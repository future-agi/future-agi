"""Questions asked about a run's calls, from the run page.

A person picks calls (the whole run, or the calls they ticked) and up to five
typed questions. ``check`` shows what the ask would read and whether it could
launch, and writes nothing. The launch runs the same check again and is refused
when what the person saw has changed. Reading an ask returns requested, the six
counts and one row per call and question. Nothing here calls a model: grading
runs in the ask's own queued job.

Every route goes through the run first (``_execution_for_request``), so a run of
another organisation or workspace is not found, and an ask is only ever read
through a run its selection names.
"""

from __future__ import annotations

from typing import Any

import structlog
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import viewer_denied
from simulate.models.ask import SimulateAsk
from simulate.models.eval_result_store import SimulateEvalResult
from simulate.models.test_execution import TestExecution
from simulate.serializers.run_ask import (
    AskCheckRequestSerializer,
    AskCheckResponseSerializer,
    AskDetailResponseSerializer,
    AskLaunchRequestSerializer,
    AskLaunchResponseSerializer,
    AskListResponseSerializer,
)
from simulate.services import ask_engine
from simulate.services.ask_selection import (
    LaunchRefused,
    Question,
    SelectionKind,
    SelectionRefused,
    check_before_launch,
    freeze_selection,
    launch_gate,
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


def _definitions(questions: list[dict]) -> list[ask_engine.Definition]:
    return [
        ask_engine.typed_question(
            f"q{index}",
            one["question"],
            kinds=one["kinds"],
            answer_type=one["answer_type"],
            labels=one.get("labels") or (),
            descriptions=one.get("descriptions") or None,
        )
        for index, one in enumerate(questions, start=1)
    ]


def _freeze(request, execution: TestExecution, selection: dict):
    return freeze_selection(
        kind=SelectionKind(selection["kind"]),
        organization_id=_organization(request).id,
        workspace_id=_workspace_id(request, execution),
        run_ids=[str(execution.id)],
        note=selection.get("note") or "",
        call_ids=[str(one) for one in selection.get("call_ids") or ()],
        asked_by_id=request.user.id,
    )


def _asks_of(request, execution: TestExecution):
    return SimulateAsk._base_manager.filter(
        organization=_organization(request),
        workspace_id=_workspace_id(request, execution),
        selection__description__run_ids__contains=[str(execution.id)],
    ).select_related("asked_by")


def _summary(ask: SimulateAsk) -> dict[str, Any]:
    counts = ask_engine.result_counts(ask)
    selection = ask.selection or {}
    description = selection.get("description") or {}
    asked_by = ask.asked_by
    return {
        "id": ask.id,
        "status": ask.status,
        "stop_cause": ask.stop_cause,
        "created_at": ask.created_at,
        "asked_by": (getattr(asked_by, "name", "") or asked_by.email)
        if asked_by is not None
        else None,
        "selection_kind": selection.get("kind") or "",
        "note": description.get("note") or "",
        "requested": len(selection.get("call_ids") or []),
        "credits_estimate": ask.credits_estimate,
        "questions": [
            {
                "key": question["key"],
                "name": question["name"],
                "answer_type": question["answer_type"],
                "labels": question.get("labels") or [],
                "kinds": [kind for _, kind in question.get("inputs") or []],
                "counts": counts[question["key"]],
            }
            for question in ask.questions
        ],
    }


def _rows(ask: SimulateAsk) -> list[dict[str, Any]]:
    """The ask's own rows, in the order the calls were picked and the questions
    asked. A row whose call or run is deleted is left out."""
    rows = SimulateEvalResult._base_manager.filter(
        ask=ask,
        origin=SimulateEvalResult.Origin.ASK,
        organization_id=ask.organization_id,
        workspace_id=ask.workspace_id,
        call_execution__deleted=False,
        call_execution__test_execution__deleted=False,
        call_execution__test_execution__run_test__deleted=False,
    ).values(
        "call_execution_id",
        "call_execution__test_execution_id",
        "call_execution__scenario__name",
        "call_execution__call_metadata__harness_scenario_key",
        "call_execution__call_metadata__harness_trial_index",
        "question_key",
        "state",
        "cause",
        "label",
        "score",
        "confidence",
        "reason",
        "finished_at",
    )
    call_order = {
        call_id: index
        for index, call_id in enumerate((ask.selection or {}).get("call_ids") or [])
    }
    question_order = {
        question["key"]: index for index, question in enumerate(ask.questions)
    }
    ordered = sorted(
        rows,
        key=lambda row: (
            call_order.get(str(row["call_execution_id"]), len(call_order)),
            question_order.get(row["question_key"], len(question_order)),
        ),
    )
    return [
        {
            **{
                key: value
                for key, value in row.items()
                if not key.startswith("call_execution__")
            },
            "test_execution_id": row["call_execution__test_execution_id"],
            "call_name": _call_name(row),
        }
        for row in ordered
    ]


def _call_name(row: dict[str, Any]) -> str:
    """The call as the run's table names it: its scenario, and its trial."""
    name = (
        row["call_execution__call_metadata__harness_scenario_key"]
        or row["call_execution__scenario__name"]
        or "Untitled scenario"
    )
    trial = row["call_execution__call_metadata__harness_trial_index"]
    return f"{name} · Trial {trial}" if trial else str(name)


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
        organization = _organization(request)
        try:
            definitions = _definitions(data["questions"])
            selection = _freeze(request, execution, data["selection"])
            questions = [
                Question(one.name, one.kinds, one.decision_model) for one in definitions
            ]
            shown = check_before_launch(
                selection,
                questions,
                organization=organization,
                workspace_id=selection.workspace_id,
            )
        except SelectionRefused as refusal:
            return _refused(refusal)
        try:
            launch_gate(
                organization=organization,
                workspace_id=selection.workspace_id,
                questions=questions,
                answers=shown["answers"],
            )
            refusal = None
        except LaunchRefused as refused:
            refusal = {"code": refused.code, "message": refused.message}
        return Response(AskCheckResponseSerializer({**shown, "refusal": refusal}).data)


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
            AskListResponseSerializer({"asks": [_summary(ask) for ask in asks]}).data
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
        accepted = data["accepted"]
        try:
            definitions = _definitions(data["questions"])
            selection = _freeze(request, execution, data["selection"])
            if len(selection.call_ids) != accepted["requested"]:
                raise LaunchRefused(
                    "selection_changed",
                    "The calls in this selection changed since it was checked. "
                    "Check it again.",
                    requested=len(selection.call_ids),
                )
            ask = ask_engine.launch_ask(
                selection,
                definitions,
                organization=_organization(request),
                workspace_id=selection.workspace_id,
                asked_by=request.user,
                accepted_credits=accepted["credits_estimate"],
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
            AskDetailResponseSerializer({**_summary(ask), "results": _rows(ask)}).data
        )
