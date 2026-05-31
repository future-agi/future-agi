import structlog
from django.core.exceptions import ImproperlyConfigured, ValidationError
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers.contracts import ACCOUNTS_ERROR_RESPONSES
from accounts.serializers.onboarding import (
    ActivationEventRequestSerializer,
    ActivationEventResponseSerializer,
    ActivationGoalConflictResponseSerializer,
    ActivationGoalRequestSerializer,
    ActivationStateApiResponseSerializer,
    ActivationStateQuerySerializer,
    OnboardingActivationFactReceiptApiResponseSerializer,
    OnboardingActivationFactReceiptRequestSerializer,
    SampleProjectApiResponseSerializer,
    SampleProjectHideRequestSerializer,
    SampleProjectRequestSerializer,
)
from accounts.services.onboarding.activation_events import (
    build_idempotency_key,
    record_event,
)
from accounts.services.onboarding.activation_fact_receipts import (
    activation_fact_body,
    receive_activation_fact,
    record_activation_fact_rejection,
)
from accounts.services.onboarding.activation_state import (
    resolve_activation_state_for_request,
)
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.goals import (
    OnboardingGoalConflict,
    save_onboarding_goal,
)
from accounts.services.onboarding.sample_project import (
    create_or_get_sample_project,
    hide_sample_project,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

EMAIL_CONTEXT_METADATA_FIELDS = (
    "campaign_key",
    "email_key",
    "send_log_id",
    "email_status",
    "target_stage",
    "target_event",
    "link_issued_at",
    "stale_reason",
    "context_status",
    "quick_start_goal",
    "quick_start_id",
    "quick_start_primary_path",
)


def _bad_request(gm, detail):
    if isinstance(detail, ValidationError):
        detail = (
            detail.message_dict if hasattr(detail, "message_dict") else detail.messages
        )
    return gm.bad_request(detail)


def _email_context_metadata(data):
    return {
        key: data.get(key)
        for key in EMAIL_CONTEXT_METADATA_FIELDS
        if data.get(key) not in {None, ""}
    }


def _get_observe_trace(*, organization, workspace, project_id, trace_id):
    from tracer.models.project import Project
    from tracer.models.trace import Trace

    try:
        project = (
            Project.no_workspace_objects.filter(
                id=project_id,
                organization=organization,
                workspace=workspace,
                trace_type="observe",
            )
            .only("id")
            .first()
        )
    except (TypeError, ValueError, ValidationError):
        project = None
    if project is None:
        raise ValidationError({"project_id": "Observe project not found."})

    try:
        trace = (
            Trace.no_workspace_objects.filter(
                id=trace_id,
                project=project,
            )
            .only("id")
            .first()
        )
    except (TypeError, ValueError, ValidationError):
        trace = None
    if trace is None:
        raise ValidationError({"artifact_id": "Trace not found."})
    return trace


class ActivationStateView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=ActivationStateQuerySerializer,
        responses={
            200: ActivationStateApiResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
    )
    def get(self, request):
        try:
            payload = resolve_activation_state_for_request(request)
            return self._gm.success_response(payload)
        except Exception as exc:
            user = getattr(request, "user", None)
            organization = getattr(request, "organization", None)
            workspace = getattr(request, "workspace", None)
            logger.exception(
                "Activation state resolution failed",
                error=str(exc),
                user_id=str(getattr(user, "id", "")),
                organization_id=str(getattr(organization, "id", "")),
                workspace_id=str(getattr(workspace, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to fetch activation state"
            )


class OnboardingGoalView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=ActivationGoalRequestSerializer,
        responses={
            200: ActivationStateApiResponseSerializer,
            409: ActivationGoalConflictResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
    )
    def post(self, request):
        serializer = ActivationGoalRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)

        try:
            context = resolve_onboarding_context(request)
            data = serializer.validated_data
            save_onboarding_goal(
                user=request.user,
                organization=context.organization,
                workspace=context.workspace,
                goal=data["goal"],
                primary_path=data.get("primary_path"),
                source=data.get("source") or "goal_picker",
                reason=data.get("reason") or "first_selection",
                metadata={
                    "campaign_key": data.get("campaign_key"),
                    "persona": data.get("persona"),
                },
                expected_stage=data.get("expected_stage"),
                known_goal_id=data.get("known_goal_id"),
            )
            payload = resolve_activation_state_for_request(request)
            return self._gm.success_response(payload)
        except OnboardingGoalConflict as exc:
            payload = resolve_activation_state_for_request(request)
            return Response(
                {
                    "status": False,
                    "result": {
                        "error_code": "ONBOARDING_GOAL_CONFLICT",
                        "reason": exc.reason,
                        "current_goal_id": (
                            str(exc.current_goal.id) if exc.current_goal else None
                        ),
                        "activation_state": payload,
                    },
                },
                status=status.HTTP_409_CONFLICT,
            )
        except ValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            return self._gm.bad_request(detail)
        except Exception as exc:
            logger.exception(
                "Onboarding goal save failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to save onboarding goal"
            )


class ActivationEventView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=ActivationEventRequestSerializer,
        responses={
            200: ActivationEventResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
    )
    def post(self, request):
        serializer = ActivationEventRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)

        try:
            context = resolve_onboarding_context(request)
            data = serializer.validated_data
            metadata = dict(data.get("metadata") or {})
            metadata.update(_email_context_metadata(data))
            artifact_type = data.get("artifact_type")
            artifact_id = data.get("artifact_id")
            project_id = data.get("project_id")

            if data["event_name"] == "trace_reviewed":
                _get_observe_trace(
                    organization=context.organization,
                    workspace=context.workspace,
                    project_id=project_id,
                    trace_id=artifact_id,
                )
                metadata.update(
                    {
                        "artifact_type": "trace",
                        "artifact_id": str(artifact_id),
                        "project_id": str(project_id),
                    }
                )
            elif artifact_type:
                metadata.update(
                    {
                        "artifact_type": artifact_type,
                        "artifact_id": str(artifact_id) if artifact_id else None,
                        "project_id": str(project_id) if project_id else None,
                    }
                )

            idempotency_key = data.get("idempotency_key") or build_idempotency_key(
                [
                    data["event_name"],
                    getattr(context.workspace, "id", None),
                    getattr(request.user, "id", None),
                    artifact_id or project_id,
                ]
            )
            event = record_event(
                user=request.user,
                organization=context.organization,
                workspace=context.workspace,
                event_name=data["event_name"],
                source=data.get("source") or "onboarding_home",
                product_path=data.get("primary_path"),
                activation_stage=data.get("stage"),
                metadata=metadata,
                is_sample=data.get("is_sample", False),
                idempotency_key=idempotency_key,
            )
            payload = resolve_activation_state_for_request(request)
            return self._gm.success_response(
                {
                    "event_id": str(event.id),
                    "event_name": event.event_name,
                    "activation_state": payload,
                }
            )
        except ValidationError as exc:
            return _bad_request(self._gm, exc)
        except Exception as exc:
            logger.exception(
                "Onboarding activation event record failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to record onboarding activation event"
            )


class OnboardingActivationFactReceiptView(APIView):
    authentication_classes = []
    permission_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=OnboardingActivationFactReceiptRequestSerializer,
        responses={
            200: OnboardingActivationFactReceiptApiResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
        reject_unknown_fields=True,
    )
    def post(self, request):
        body = activation_fact_body(request.data)
        serializer = OnboardingActivationFactReceiptRequestSerializer(data=request.data)
        if not serializer.is_valid():
            record_activation_fact_rejection(
                reason="malformed_payload",
                message="Activation fact request did not match the contract.",
                payload=request.data if isinstance(request.data, dict) else None,
                headers=request.headers,
                body=body,
            )
            return self._gm.bad_request(serializer.errors)

        try:
            result = receive_activation_fact(
                payload=serializer.validated_data,
                headers=request.headers,
                body=body,
            )
            receipt = result.receipt
            return self._gm.success_response(
                {
                    "receipt_id": str(receipt.id),
                    "created": result.created,
                    "idempotency_key": receipt.idempotency_key,
                    "workspace_id": str(receipt.workspace_id_value),
                    "user_id": (
                        str(receipt.user_id_value) if receipt.user_id_value else None
                    ),
                    "activation_stage": receipt.activation_stage,
                    "primary_path": receipt.primary_path,
                    "cohort_keys": receipt.cohort_keys,
                }
            )
        except ValidationError as exc:
            return _bad_request(self._gm, exc)
        except ImproperlyConfigured as exc:
            logger.exception(
                "Activation fact receiver configuration failed",
                error=str(exc),
            )
            return self._gm.internal_server_error_response(
                "Activation fact receiver is not configured"
            )
        except Exception as exc:
            logger.exception(
                "Activation fact receipt failed",
                error=str(exc),
            )
            return self._gm.internal_server_error_response(
                "Failed to record activation fact"
            )


class SampleProjectView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=SampleProjectRequestSerializer,
        responses={
            200: SampleProjectApiResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
    )
    def post(self, request):
        serializer = SampleProjectRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)

        try:
            context = resolve_onboarding_context(request)
            flags = get_onboarding_flags(
                user=request.user,
                organization=context.organization,
                workspace=context.workspace,
            )
            data = serializer.validated_data
            sample_project = create_or_get_sample_project(
                request.user,
                context.organization,
                context.workspace,
                source=data.get("source") or "onboarding_home",
                reason=data.get("reason") or "manual_open",
                is_enabled=bool(flags.get("onboarding_sample_project")),
                can_create=context.permissions["can_write"],
                manifest_id=data.get("manifest_id"),
                manifest_version=data.get("manifest_version"),
                email_context=_email_context_metadata(data),
            )
            activation_state = resolve_activation_state_for_request(request)
            return self._gm.success_response(
                {
                    "sample_project": sample_project,
                    "activation_state": activation_state,
                }
            )
        except ValidationError as exc:
            return _bad_request(self._gm, exc)
        except Exception as exc:
            logger.exception(
                "Onboarding sample project open failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to open sample project"
            )


class SampleProjectHideView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=SampleProjectHideRequestSerializer,
        responses={
            200: SampleProjectApiResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
    )
    def post(self, request):
        serializer = SampleProjectHideRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return self._gm.bad_request(serializer.errors)

        try:
            context = resolve_onboarding_context(request)
            flags = get_onboarding_flags(
                user=request.user,
                organization=context.organization,
                workspace=context.workspace,
            )
            data = serializer.validated_data
            sample_project = hide_sample_project(
                request.user,
                context.organization,
                context.workspace,
                source=data.get("source") or "onboarding_home",
                reason=data.get("reason") or "user_dismissed",
                is_enabled=bool(flags.get("onboarding_sample_project")),
            )
            activation_state = resolve_activation_state_for_request(request)
            return self._gm.success_response(
                {
                    "sample_project": sample_project,
                    "activation_state": activation_state,
                }
            )
        except ValidationError as exc:
            return _bad_request(self._gm, exc)
        except Exception as exc:
            logger.exception(
                "Onboarding sample project hide failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to hide sample project"
            )
