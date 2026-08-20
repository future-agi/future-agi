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

import os

import structlog
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from accounts.authentication import APIKeyAuthentication
from simulate.authentication import InternalServiceAuthentication
from simulate.models import CallExecution, RunTest, SimulatorAgent, TestExecution
from simulate.serializers.alk_simulate_ingestion import (
    ALKSimulateBatchCreateRequestSerializer,
    ALKSimulateBatchCreateResponseSerializer,
    ALKSimulateProvisionResponseSerializer,
    ALKSimulateProvisionRunTestRequestSerializer,
    ALKSimulateRecordingUploadRequestSerializer,
    ALKSimulateRecordingUploadResponseSerializer,
    ALKSimulateResultResponseSerializer,
    ALKSimulateResultSerializer,
    ALKSimulateStartTestExecutionRequestSerializer,
    ALKSimulateStartTestExecutionResponseSerializer,
    ALKSimulateStatusUpdateResponseSerializer,
    ALKSimulateStatusUpdateSerializer,
)
from simulate.services.alk_simulate_ingestion import (
    ALKSimulateIngestionError,
    create_alk_sim_call_execution_batch,
    create_alk_sim_test_execution,
    ingest_alk_sim_result,
    mark_alk_sim_call_ongoing,
    provision_alk_sim_run_test,
    store_alk_recording,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods

# Reject oversized recording uploads before reading them into memory (the read
# holds ~3x the file resident, and FILE_UPLOAD_MAX_MEMORY_SIZE is 1 GB). The
# default covers the model's max call (max_call_duration_in_minutes ≤ 180): an
# 8 kHz 16-bit WAV is ~1 MB/min mono, ~2 MB/min stereo, so 180 min ≈ 345 MB.
# Env-tunable for higher sample rates. NOTE: the proper DoS fix is to stream the
# upload straight to storage (uploaded.chunks()) rather than read() it whole —
# deferred; this ceiling bounds the blast radius in the meantime.
_MAX_RECORDING_UPLOAD_BYTES = (
    int(os.getenv("ALK_MAX_RECORDING_UPLOAD_MB", "512")) * 1024 * 1024
)

logger = structlog.get_logger(__name__)


class ALKSimulateIngestionViewSet(ViewSet):
    """Single view surface for all LiveKit sim ingestion HTTP endpoints.

    Views here are intentionally minimal: they resolve the tenant-scoped
    target row, hand the parsed payload to
    `simulate.services.alk_simulate_ingestion`, and format the response.
    """

    authentication_classes = [InternalServiceAuthentication, APIKeyAuthentication]
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

    def _is_internal_service(self, request):
        return bool(getattr(request.user, "is_internal_service", False))

    def _test_execution_or_404(self, test_execution_id, request):
        try:
            test_execution = TestExecution.objects.select_related(
                "run_test",
                "run_test__agent_definition",
                "run_test__organization",
                "agent_version",
            ).get(id=test_execution_id, deleted=False)
        except TestExecution.DoesNotExist as exc:
            raise Http404 from exc
        execution_organization = test_execution.run_test.organization
        if self._is_internal_service(request):
            return test_execution, execution_organization

        organization = self._resolve_organization(request)
        if organization is None or execution_organization.id != organization.id:
            raise Http404
        return test_execution, organization

    def _call_execution_or_404(self, call_execution_id, request):
        call_execution = get_object_or_404(
            CallExecution.objects.select_related(
                "test_execution",
                "test_execution__run_test",
                "test_execution__run_test__organization",
            ),
            id=call_execution_id,
            deleted=False,
        )
        execution_organization = call_execution.test_execution.run_test.organization
        if self._is_internal_service(request):
            return call_execution, execution_organization

        organization = self._resolve_organization(request)
        if organization is None or execution_organization.id != organization.id:
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

    @action(detail=False, methods=["post"], url_path=r"run-tests/provision")
    @validated_request(
        request_serializer=ALKSimulateProvisionRunTestRequestSerializer,
        responses={
            200: ALKSimulateProvisionResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def provision_run_test(self, request):
        """Stand up a chat RunTest + scenario-of-record from SDK personas so an
        SDK-first run has somewhere to post — without the native UI's async
        scenario generation. See ``provision_alk_sim_run_test``."""
        organization = self._resolve_organization(request)
        if organization is None:
            return self.gm.not_found("Organization not found")

        payload = request.validated_data
        try:
            run_test, scenarios, agent_definition = provision_alk_sim_run_test(
                organization,
                name=payload["name"],
                personas=payload.get("personas"),
                scenario_ids=payload.get("scenario_ids"),
                agent_definition_id=payload.get("agent_definition_id"),
                agent_name=payload.get("agent_name"),
                description=payload.get("description", ""),
            )
        except ALKSimulateIngestionError as e:
            return self.gm.bad_request(str(e))
        except Exception:
            logger.exception("alk_provision_run_test_failed")
            return self.gm.internal_server_error_response(
                "Failed to provision ALK run test"
            )

        return self.gm.success_response(
            {
                "run_test_id": str(run_test.id),
                "scenario_ids": [str(s.id) for s in scenarios],
                "agent_definition_id": str(agent_definition.id),
            }
        )

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
        request_serializer=ALKSimulateBatchCreateRequestSerializer,
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
            result = create_alk_sim_call_execution_batch(
                test_execution, count=request.validated_data.get("count")
            )
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
    @validated_request(
        request_serializer=ALKSimulateRecordingUploadRequestSerializer,
        responses={
            200: ALKSimulateRecordingUploadResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
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

        size = getattr(uploaded, "size", None)
        if size is not None and size > _MAX_RECORDING_UPLOAD_BYTES:
            return self.gm.bad_request(
                f"recording exceeds the {_MAX_RECORDING_UPLOAD_BYTES // (1024 * 1024)} MB limit"
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

    @action(
        detail=False,
        methods=["patch"],
        url_path=r"call-executions/(?P<call_execution_id>[0-9a-fA-F-]{36})/status",
    )
    @validated_request(
        request_serializer=ALKSimulateStatusUpdateSerializer,
        responses={
            200: ALKSimulateStatusUpdateResponseSerializer,
            400: ApiTextErrorResponseSerializer,
            404: ApiTextErrorResponseSerializer,
            500: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def status(self, request, call_execution_id=None):
        """Non-terminal per-call status ping (currently only ``ongoing``): the
        SDK marks a pre-created PENDING row ONGOING the moment its call starts,
        so the UI shows progress instead of PENDING → terminal. PENDING-gated in
        the service, so a late ping never clobbers a result that already landed.
        """
        try:
            call_execution, _ = self._call_execution_or_404(call_execution_id, request)
        except Http404:
            return self.gm.not_found("Call execution not found")

        updated = mark_alk_sim_call_ongoing(call_execution)
        return self.gm.success_response({"updated": updated})
