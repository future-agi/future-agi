import structlog
from django.shortcuts import get_object_or_404
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.models.develop_dataset import Row
from simulate.models import CallExecution
from simulate.serializers.response.call_execution import (
    CallExecutionErrorResponseSerializer,
    SessionComparisonResponseSerializer,
)
from simulate.utils.session_comparison import (
    fetch_comparison_metrics,
    fetch_comparison_recordings,
    fetch_comparison_transcripts,
    fetch_voice_conversation_span,
    fetch_voice_trace_comparison_metrics,
    fetch_voice_trace_comparison_transcripts,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.errors import format_validation_error
from tfc.utils.general_methods import GeneralMethods
from tracer.models.replay_session import ReplaySession

logger = structlog.get_logger(__name__)


class SessionComparisonChatSimView(APIView):
    """
    API View to compare session chat simulations
    """

    permission_classes = [IsAuthenticated]
    serializer_class = None
    _gm = GeneralMethods()

    def _validate(self, request, call_exec) -> tuple[str, str]:
        """
        Validate the call execution and derive the baseline identifier.

        Returns:
            Tuple of (baseline_id, baseline_type) where:
            - baseline_type is "session" (multi-trace session) or "trace" (single voice trace)
            - baseline_id is the session_id or trace_id
        """
        if not call_exec:
            raise ValidationError("Call execution not found")
        if call_exec.simulation_call_type not in (
            CallExecution.SimulationCallType.TEXT,
            CallExecution.SimulationCallType.VOICE,
        ):
            raise ValidationError("Unsupported call execution type")
        if call_exec.status != CallExecution.CallStatus.COMPLETED:
            raise ValidationError("Call execution is not completed yet")

        row_id = call_exec.row_id
        if not row_id:
            raise ValidationError("Row ID is not associated which is required")
        row = get_object_or_404(
            Row,
            id=row_id,
            dataset__organization=getattr(request, "organization", None)
            or request.user.organization,
        )
        if not row:
            raise ValidationError("Row not found")

        metadata = row.metadata or {}
        is_voice = (
            call_exec.simulation_call_type == CallExecution.SimulationCallType.VOICE
        )

        if is_voice:
            # Voice replay: look for baseline trace ID
            trace_id = metadata.get("trace_id") or metadata.get("intent_id")
            if trace_id:
                return trace_id, "trace"

            # Fallback: look up via replay session
            run_test = None
            if getattr(call_exec, "test_execution_id", None):
                try:
                    run_test = call_exec.test_execution.run_test
                except (AttributeError, CallExecution.DoesNotExist):
                    pass
            if run_test:
                replay_session = ReplaySession.objects.filter(
                    run_test=run_test,
                    replay_type="trace",
                ).first()
                if replay_session and replay_session.ids:
                    return str(replay_session.ids[0]), "trace"

            raise ValidationError("Comparison is only available for replay sessions")

        # Chat replay: look for session_id
        session_id = metadata.get("session_id")
        if not session_id:
            raise ValidationError("No session ID found for comparison")
        return session_id, "session"

    @swagger_auto_schema(
        responses={
            200: SessionComparisonResponseSerializer,
            400: CallExecutionErrorResponseSerializer,
            500: CallExecutionErrorResponseSerializer,
        },
    )
    def get(self, request, call_execution_id, *args, **kwargs):
        try:
            call_exec = get_object_or_404(
                CallExecution,
                id=call_execution_id,
                test_execution__run_test__organization=getattr(
                    request, "organization", None
                )
                or request.user.organization,
            )

            baseline_id, baseline_type = self._validate(
                request=request, call_exec=call_exec
            )

            if baseline_type == "trace":
                # Fetch the conversation span once and reuse across all extractors
                span = fetch_voice_conversation_span(baseline_id)
                result_metrics = fetch_voice_trace_comparison_metrics(
                    call_exec, baseline_id, _span=span
                )
                comparison_transcripts = fetch_voice_trace_comparison_transcripts(
                    call_exec, baseline_id, _span=span
                )
                comparison_recordings = fetch_comparison_recordings(
                    call_exec, baseline_id, _span=span
                )
            else:
                result_metrics = fetch_comparison_metrics(call_exec, baseline_id)
                comparison_transcripts = fetch_comparison_transcripts(
                    call_exec, baseline_id
                )
                comparison_recordings = None

            logger.info(
                "Result metrics calculated successfully",
                baseline_id=baseline_id,
                call_exec_id=str(call_exec.id),
            )

            result = {
                "comparison_metrics": result_metrics,
                "comparison_transcripts": comparison_transcripts,
            }

            if comparison_recordings:
                result["comparison_recordings"] = comparison_recordings

            return self._gm.success_response(result)
        except ValidationError as e:
            logger.exception("Validation error", error=str(e))
            return self._gm.bad_request(format_validation_error(e))
        except Exception as e:
            logger.exception("Error comparing session chat simulations", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("SESSION_COMPARISON_FAILED_CHAT_SIM")
            )
