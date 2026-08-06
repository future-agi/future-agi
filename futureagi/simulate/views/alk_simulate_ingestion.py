"""HTTP surface for ALK sim ingestion. Delegates all logic to services.

Endpoints exposed by `ALKSimulateIngestionViewSet` (mounted under the simulate
router at `simulate/api/alk-simulate/`):

  POST   test-executions/<uuid>/batch/       → batch-create PENDING VOICE rows
  PATCH  call-executions/<uuid>/result/      → ingest a completed sim result

Recording/artifact URLs are supplied by the client as strings pointing at its
own storage (same pattern the Vapi provider adapter uses — we save URLs, the
backend never touches the bytes).
"""

from __future__ import annotations

import structlog
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from simulate.models import CallExecution, RunTest, SimulatorAgent, TestExecution
from simulate.serializers.alk_simulate_ingestion import (
    ALKSimulateBatchCreateResponseSerializer,
    ALKSimulateResultResponseSerializer,
    ALKSimulateResultSerializer,
    ALKSimulateStartTestExecutionRequestSerializer,
    ALKSimulateStartTestExecutionResponseSerializer,
)
from simulate.services.alk_simulate_ingestion import (
    ALKSimulateIngestionError,
    create_alk_sim_call_execution_batch,
    create_alk_sim_test_execution,
    ingest_alk_sim_result,
    store_alk_recording,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    ApiTextErrorResponseSerializer,
    EmptyRequestSerializer,
)
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class ALKSimulateIngestionViewSet(ViewSet):
    """Single view surface for all LiveKit sim ingestion HTTP endpoints.

    Views here are intentionally minimal: they resolve the tenant-scoped
    target row, hand the parsed payload to
    `simulate.services.alk_simulate_ingestion`, and format the response.
    """

    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.gm = GeneralMethods()

    # -- helpers ----------------------------------------------------------------

    def _resolve_organization(self, request):
        org = getattr(request, "organization", None)
        if org is not None:
            return org
        user = getattr(request, "user", None)
        return getattr(user, "organization", None) if user is not None else None

    def _test_execution_or_404(self, test_execution_id, request):
        try:
            test_execution = TestExecution.objects.select_related(
                "run_test", "run_test__agent_definition", "agent_version"
            ).get(id=test_execution_id, deleted=False)
        except TestExecution.DoesNotExist as exc:
            raise Http404 from exc
        organization = self._resolve_organization(request)
        if organization is None or (
            test_execution.run_test.organization_id != organization.id
        ):
            raise Http404
        return test_execution, organization

    def _call_execution_or_404(self, call_execution_id, request):
        call_execution = get_object_or_404(
            CallExecution.objects.select_related(
                "test_execution", "test_execution__run_test"
            ),
            id=call_execution_id,
            deleted=False,
        )
        organization = self._resolve_organization(request)
        if organization is None or (
            call_execution.test_execution.run_test.organization_id != organization.id
        ):
            raise Http404
        return call_execution, organization

    def _run_test_or_404(self, run_test_id, request):
        try:
            run_test = RunTest.objects.select_related(
                "agent_definition", "agent_version", "simulator_agent"
            ).get(id=run_test_id, deleted=False)
        except RunTest.DoesNotExist as exc:
            raise Http404 from exc
        organization = self._resolve_organization(request)
        if organization is None or run_test.organization_id != organization.id:
            raise Http404
        return run_test, organization

    # -- endpoints --------------------------------------------------------------

    @action(
        detail=False,
        methods=["post"],
        url_path=r"run-tests/(?P<run_test_id>[0-9a-fA-F-]{36})/test-executions",
    )
    @validated_request(
        request_serializer=ALKSimulateStartTestExecutionRequestSerializer,
        responses={
            200: ALKSimulateStartTestExecutionResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def start_test_execution(self, request, run_test_id=None):
        try:
            run_test, _ = self._run_test_or_404(run_test_id, request)
        except Http404:
            return self.gm.not_found("Run test not found")

        payload = request.validated_data
        simulator_agent = None
        simulator_agent_id = payload.get("simulator_agent_id")
        if simulator_agent_id:
            try:
                simulator_agent = SimulatorAgent.objects.get(
                    id=simulator_agent_id, deleted=False
                )
            except SimulatorAgent.DoesNotExist:
                return self.gm.bad_request(
                    f"Simulator agent {simulator_agent_id} not found"
                )

        try:
            test_execution = create_alk_sim_test_execution(
                run_test,
                scenario_ids=payload.get("scenario_ids") or None,
                simulator_agent=simulator_agent,
            )
        except ALKSimulateIngestionError as e:
            return self.gm.bad_request(str(e))
        except Exception:
            logger.exception(
                "alk_start_test_execution_failed", run_test_id=str(run_test_id)
            )
            return self.gm.internal_server_error_response(
                "Failed to start ALK test execution"
            )

        return self.gm.success_response(
            {
                "test_execution_id": str(test_execution.id),
                "run_test_id": str(run_test.id),
                "scenario_ids": [str(sid) for sid in test_execution.scenario_ids],
                "total_scenarios": test_execution.total_scenarios,
                "status": test_execution.status,
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path=r"test-executions/(?P<test_execution_id>[0-9a-fA-F-]{36})/batch",
    )
    @validated_request(
        request_serializer=EmptyRequestSerializer,
        responses={
            200: ALKSimulateBatchCreateResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def batch(self, request, test_execution_id=None):
        try:
            test_execution, _ = self._test_execution_or_404(test_execution_id, request)
        except Http404:
            return self.gm.not_found("Test execution not found")

        try:
            result = create_alk_sim_call_execution_batch(test_execution)
        except ALKSimulateIngestionError as e:
            return self.gm.bad_request(str(e))
        except Exception:
            logger.exception(
                "livekit_batch_create_failed",
                test_execution_id=str(test_execution_id),
            )
            return self.gm.internal_server_error_response(
                "Failed to create LiveKit call execution batch"
            )

        return self.gm.success_response(
            {
                "call_execution_ids": result.call_execution_ids,
                "has_more": result.has_more,
                "batched_scenarios": result.batched_scenarios,
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path=r"call-executions/(?P<call_execution_id>[0-9a-fA-F-]{36})/recording",
        parser_classes=[MultiPartParser],
    )
    def recording_upload(self, request, call_execution_id=None):
        """Accept a multipart audio upload and hand it to the shared voice
        storage helper (``upload_audio_to_s3``). Matches the pattern the
        LiveKit and Vapi voice services already use for their recordings.
        """
        try:
            call_execution, _ = self._call_execution_or_404(call_execution_id, request)
        except Http404:
            return self.gm.not_found("Call execution not found")

        uploaded = request.FILES.get("file")
        if uploaded is None:
            return self.gm.bad_request(
                "recording upload requires a 'file' multipart field"
            )

        try:
            audio_bytes = uploaded.read()
        except Exception:
            logger.exception(
                "alk_recording_upload_read_failed",
                call_execution_id=str(call_execution_id),
            )
            return self.gm.bad_request("failed to read uploaded recording")

        filename = (
            request.data.get("filename") or getattr(uploaded, "name", None) or None
        )

        try:
            outcome = store_alk_recording(
                call_execution,
                audio_bytes,
                filename=filename,
            )
        except ALKSimulateIngestionError as e:
            return self.gm.bad_request(str(e))
        except Exception:
            logger.exception(
                "alk_recording_upload_failed",
                call_execution_id=str(call_execution_id),
            )
            return self.gm.internal_server_error_response("Failed to persist recording")

        return self.gm.success_response(
            {
                "recording_url": outcome.recording_url,
                "object_key": outcome.object_key,
            }
        )

    @action(
        detail=False,
        methods=["patch"],
        url_path=r"call-executions/(?P<call_execution_id>[0-9a-fA-F-]{36})/result",
    )
    @validated_request(
        request_serializer=ALKSimulateResultSerializer,
        responses={
            200: ALKSimulateResultResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def result(self, request, call_execution_id=None):
        try:
            call_execution, organization = self._call_execution_or_404(
                call_execution_id, request
            )
        except Http404:
            return self.gm.not_found("Call execution not found")

        try:
            outcome = ingest_alk_sim_result(
                call_execution, organization, request.validated_data
            )
        except ALKSimulateIngestionError as e:
            return self.gm.bad_request(str(e))
        except Exception:
            logger.exception(
                "livekit_result_ingest_failed",
                call_execution_id=str(call_execution_id),
            )
            return self.gm.internal_server_error_response(
                "Failed to ingest LiveKit result"
            )

        return self.gm.success_response(
            {
                "call_execution_id": outcome.call_execution_id,
                "status": outcome.status,
                "eval_dispatched": outcome.eval_dispatched,
            }
        )
