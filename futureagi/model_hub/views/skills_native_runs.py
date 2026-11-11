"""Public application-verification APIs and the authenticated native guest channel."""

from uuid import UUID

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.serializers import skills_native_runs as s
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3 import native_runs
from model_hub.services.skills_hub.v11.principals import require_private_principal
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView
from simulate.authentication import HarnessAttemptAuthentication
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.general_methods import GeneralMethods


class NativeContextView(SkillsView):
    private_resource = True

    def initial(self, request: Request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        require_private_principal(principal=request.skill_principal)
        get_resource(
            principal=request.skill_principal,
            resource_id=kwargs["context_id"],
            kind="agent_context",
        )

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response


class NativeTargetsView(NativeContextView):
    @contract(s.ApplicationTargets)
    def get(self, request: Request, context_id: UUID) -> Response:
        return self.output(
            request,
            s.ApplicationTargets,
            native_runs.list_application_targets(
                principal=request.skill_principal, context_id=context_id
            ),
        )


class NativeTargetView(NativeContextView):
    @contract(s.ApplicationTarget, body=s.QualifyTargetInput)
    def put(self, request: Request, context_id: UUID, binding_id: UUID) -> Response:
        return self.output(
            request,
            s.ApplicationTarget,
            native_runs.qualify_application_target(
                principal=request.skill_principal,
                binding_id=binding_id,
                revision=request.validated_data["revision"],
                configuration=request.validated_data["configuration"],
            ),
        )


class NativePreviewView(NativeContextView):
    @contract(s.NativePlan, body=s.NativePlanInput)
    def post(self, request: Request, context_id: UUID) -> Response:
        return self.output(
            request,
            s.NativePlan,
            native_runs.preview_native_run(
                principal=request.skill_principal,
                context_id=context_id,
                request=_plain(request.validated_data),
            ),
        )


class NativeRunsView(NativeContextView):
    @contract(s.NativeRunList)
    def get(self, request: Request, context_id: UUID) -> Response:
        return self.output(
            request,
            s.NativeRunList,
            native_runs.list_native_runs(
                principal=request.skill_principal, context_id=context_id
            ),
        )

    @contract(
        s.NativeRunCreated,
        body=s.NativeRunInput,
        status=202,
        manual_parameters=[IDEMPOTENCY],
    )
    def post(self, request: Request, context_id: UUID) -> Response:
        return self.output(
            request,
            s.NativeRunCreated,
            native_runs.create_native_run(
                principal=request.skill_principal,
                context_id=context_id,
                request=_plain(request.validated_data),
                idempotency_key=header_uuid(request, "Idempotency-Key"),
            ),
            status=202,
        )


class NativeRunView(NativeContextView):
    @contract(s.NativeRunDetail)
    def get(self, request: Request, context_id: UUID, evidence_id: UUID) -> Response:
        return self.output(
            request,
            s.NativeRunDetail,
            native_runs.get_native_run(
                principal=request.skill_principal,
                context_id=context_id,
                evidence_id=evidence_id,
            ),
        )


def _plain(value):
    """Canonical JSON values: UUIDs as strings, no serializer containers."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    return value


# --- Authenticated guest channel (attempt bearer + fence; no user/API-key scope) ---


class NativeGuestView(APIView):
    authentication_classes = [HarnessAttemptAuthentication]
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, SkillsHubError):
            return Response(
                {"status": False, "error": {"code": exc.code, "message": str(exc)}},
                status=exc.status,
            )
        if isinstance(exc, HostedHarnessError):
            return Response(
                {"status": False, "error": {"code": exc.code}},
                status=exc.status_code,
            )
        return super().handle_exception(exc)

    def reply(self, request, value: dict) -> Response:
        return GeneralMethods(request).success_response(value, status=200)


class NativeGuestHealthView(NativeGuestView):
    def post(self, request: Request, attempt_id: UUID) -> Response:
        return self.reply(
            request,
            native_runs.record_guest_health(attempt=request.auth, health=request.data),
        )


class NativeGuestObservationView(NativeGuestView):
    def post(self, request: Request, attempt_id: UUID) -> Response:
        return self.reply(
            request,
            native_runs.ingest_observation(attempt=request.auth, body=request.data),
        )


class NativeGuestFailureView(NativeGuestView):
    def post(self, request: Request, attempt_id: UUID) -> Response:
        reason = request.data.get("reason") if isinstance(request.data, dict) else None
        return self.reply(
            request,
            native_runs.report_guest_failure(attempt=request.auth, reason=reason),
        )
