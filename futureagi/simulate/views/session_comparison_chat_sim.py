from uuid import UUID

import structlog
from django.core.exceptions import ValidationError as ModelValidationError
from django.http import Http404
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
from simulate.views.scoping import run_test_workspace_filter
from tfc.utils.error_codes import get_error_message
from tfc.utils.errors import format_validation_error
from tfc.utils.general_methods import GeneralMethods
from tracer.models.replay_session import ReplaySession
from tracer.utils.workspace_scope import (
    get_request_organization,
    project_queryset_for_request,
)

logger = structlog.get_logger(__name__)


class SessionComparisonChatSimView(APIView):
    """
    API View to compare session chat simulations
    """

    permission_classes = [IsAuthenticated]
    serializer_class = None
    _gm = GeneralMethods()

    def _validate(self, request, call_exec) -> tuple[str, str, str]:
        """Return the baseline ID, type, and authorized project ID.

        Row metadata and persisted replay IDs are untrusted, including replay
        sessions written before creation-time membership checks were added.
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
            Row.no_workspace_objects,
            run_test_workspace_filter(request, "dataset"),
            id=row_id,
            dataset__organization=get_request_organization(request),
            deleted=False,
            dataset__deleted=False,
        )

        metadata = row.metadata or {}
        is_voice = (
            call_exec.simulation_call_type == CallExecution.SimulationCallType.VOICE
        )
        projects = project_queryset_for_request(request)
        if is_voice:
            trace_id = metadata.get("trace_id") or metadata.get("intent_id")
            if not trace_id:
                replay_session = ReplaySession.no_workspace_objects.filter(
                    run_test=call_exec.test_execution.run_test,
                    replay_type="trace",
                    project__in=projects,
                    deleted=False,
                ).first()
                if not replay_session or not replay_session.ids:
                    raise Http404
                trace_id = replay_session.ids[0]
                # The fallback's authorized project is the replay's project,
                # not any project that happens to share its organization.
                projects = projects.filter(id=replay_session.project_id)
            from tracer.services.clickhouse.v2 import get_reader

            trace_id = str(UUID(str(trace_id)))
            project_ids = [str(pid) for pid in projects.values_list("id", flat=True)]
            if not project_ids:
                raise Http404
            with get_reader() as reader:
                project_id = reader.newest_trace_projects([trace_id], project_ids).get(
                    trace_id
                )
            if project_id not in project_ids:
                raise Http404
            return trace_id, "trace", project_id

        session_id = metadata.get("session_id")
        if not session_id:
            raise ValidationError("No session ID found for comparison")
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        session_id = str(UUID(str(session_id)))
        project_ids = [str(pid) for pid in projects.values_list("id", flat=True)]
        if not project_ids:
            raise Http404
        baseline = resolve_session_fields(
            [session_id],
            project_ids=project_ids,
            include_overlay=False,
        ).get(session_id)
        if not baseline or str(baseline["project_id"]) not in project_ids:
            raise Http404
        return session_id, "session", str(baseline["project_id"])

    @swagger_auto_schema(
        responses={
            200: SessionComparisonResponseSerializer,
            400: CallExecutionErrorResponseSerializer,
            404: CallExecutionErrorResponseSerializer,
            500: CallExecutionErrorResponseSerializer,
        },
    )
    def get(self, request, call_execution_id, *args, **kwargs):
        try:
            organization = get_request_organization(request)
            if organization is None:
                raise Http404
            call_exec = get_object_or_404(
                CallExecution.no_workspace_objects.select_related(
                    "test_execution__run_test"
                ),
                run_test_workspace_filter(request, "test_execution__run_test"),
                id=call_execution_id,
                test_execution__run_test__organization=organization,
                deleted=False,
                test_execution__deleted=False,
                test_execution__run_test__deleted=False,
            )

            baseline_id, baseline_type, project_id = self._validate(
                request=request, call_exec=call_exec
            )

            if baseline_type == "trace":
                # Fetch once, under the authorized project, for all extractors.
                span = fetch_voice_conversation_span(baseline_id, project_id=project_id)
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
                result_metrics = fetch_comparison_metrics(
                    call_exec, baseline_id, project_id=project_id
                )
                comparison_transcripts = fetch_comparison_transcripts(
                    call_exec, baseline_id, project_id=project_id
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
        except (Http404, ModelValidationError, ValueError):
            # Missing, foreign, malformed, and unavailable baselines are the
            # same categorical refusal; never include the supplied identifier.
            return self._gm.not_found("Comparison source not found.")
        except ValidationError as e:
            logger.exception("Validation error", error=str(e))
            return self._gm.bad_request(format_validation_error(e))
        except Exception as e:
            logger.exception("Error comparing session chat simulations", error=str(e))
            return self._gm.internal_server_error_response(
                get_error_message("SESSION_COMPARISON_FAILED_CHAT_SIM")
            )
