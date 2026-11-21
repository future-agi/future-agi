"""Public application-verification APIs and the authenticated native guest channel."""

from uuid import UUID

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.serializers import skills_hub as hub_serializers
from model_hub.serializers import skills_native_runs as s
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3 import native_evidence, native_runs
from model_hub.services.skills_hub.v11.principals import require_private_principal
from model_hub.views.skills_context_foundation import IDEMPOTENCY, contract, header_uuid
from model_hub.views.skills_hub import SkillsView, domain_error
from simulate.authentication import HarnessAttemptAuthentication
from simulate.models.hosted_harness import HostedHarnessAttempt
from simulate.services.hosted_harness import HostedHarnessError
from tfc.utils.api_contracts import validated_request
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

    @contract(s.ApplicationTarget, body=s.CreateTargetInput, status=201)
    def post(self, request: Request, context_id: UUID) -> Response:
        return self.output(
            request,
            s.ApplicationTarget,
            native_runs.create_application_target(
                principal=request.skill_principal,
                context_id=context_id,
                version=request.validated_data["version"],
            ),
            status=201,
        )


class NativeRuntimeConfigurationView(NativeContextView):
    @contract(s.RuntimeConfiguration)
    def get(self, request: Request, context_id: UUID) -> Response:
        return self.output(
            request,
            s.RuntimeConfiguration,
            native_runs.runtime_configuration(principal=request.skill_principal),
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


class NativeRunShareView(NativeContextView):
    @contract(s.NativeRunSummary, body=s.ShareNativeInput)
    def post(self, request: Request, context_id: UUID, evidence_id: UUID) -> Response:
        native_evidence.share_native_evidence(
            principal=request.skill_principal,
            resource_id=context_id,
            kind="agent_context",
            evidence_id=evidence_id,
            acknowledge_snapshot_sharing=request.validated_data[
                "acknowledge_snapshot_sharing"
            ],
        )
        return self.output(
            request,
            s.NativeRunSummary,
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
        # Guest refusals use the common error envelope; the runtime keys on
        # the status and the envelope's code.
        if isinstance(exc, SkillsHubError):
            return domain_error(exc)
        if isinstance(exc, HostedHarnessError):
            return GeneralMethods().custom_error_response(
                exc.status_code, str(exc), code=exc.code
            )
        return super().handle_exception(exc)

    def reply(self, request, value: dict) -> Response:
        response: Response = GeneralMethods(request).success_response(value, status=200)
        return response


def _attempt(request: Request) -> HostedHarnessAttempt:
    """The hosted attempt HarnessAttemptAuthentication fenced this request to."""
    attempt = request.auth
    if not isinstance(attempt, HostedHarnessAttempt):
        raise HostedHarnessError(
            "authentication_required",
            "a harness attempt bearer is required",
            status_code=401,
        )
    return attempt


def _guest_responses(output) -> dict:
    """The guest channel's documented replies: the envelope and every refusal it can send.

    Refusals come from the attempt bearer/fence (401, 403, 404, 409), the shape checks
    (400, 422) and the services (400, 409, 413, 422), all in the common error envelope.
    """
    responses = dict.fromkeys((400, 401, 403, 404, 413), hub_serializers.SkillsError)
    responses.update(
        {
            409: hub_serializers.SkillsConflictError,
            422: hub_serializers.SkillsChecksError,
            200: hub_serializers.envelope(output),
        }
    )
    return responses


def _guest_refusal(code: str, message: str, status: int):
    """A malformed guest body is refused with the same code the service uses."""

    def respond(_errors) -> Response:
        response: Response = GeneralMethods().custom_error_response(
            status, message, code=code
        )
        return response

    return respond


class NativeGuestHealthView(NativeGuestView):
    @validated_request(
        request_serializer=s.GuestHealthInput,
        responses=_guest_responses(s.GuestAccepted),
        reject_unknown_fields=True,
        validation_error_response=_guest_refusal(
            "runtime_incompatible",
            "Guest identity, restored state or isolation did not match the reviewed adapter.",
            422,
        ),
    )
    def post(self, request: Request, attempt_id: UUID) -> Response:
        return self.reply(
            request,
            native_runs.record_guest_health(
                attempt=_attempt(request), health=request.data
            ),
        )


class NativeGuestObservationView(NativeGuestView):
    @validated_request(
        request_serializer=s.GuestObservationInput,
        responses=_guest_responses(s.GuestObservationAccepted),
        reject_unknown_fields=True,
        validation_error_response=_guest_refusal(
            "invalid_receipt", "Use the exact native observation.", 400
        ),
    )
    def post(self, request: Request, attempt_id: UUID) -> Response:
        return self.reply(
            request,
            native_runs.ingest_observation(
                attempt=_attempt(request), body=request.data
            ),
        )


class NativeGuestFailureView(NativeGuestView):
    # Any reason is accepted; the service records an unrecognized one as guest_failed.
    @validated_request(
        request_serializer=s.GuestFailureInput,
        responses=_guest_responses(s.GuestAccepted),
        strict_request_validation=False,
    )
    def post(self, request: Request, attempt_id: UUID) -> Response:
        reason = request.data.get("reason") if isinstance(request.data, dict) else None
        return self.reply(
            request,
            native_runs.report_guest_failure(attempt=_attempt(request), reason=reason),
        )
