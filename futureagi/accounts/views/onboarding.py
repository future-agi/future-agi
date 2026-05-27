import structlog
from django.core.exceptions import ValidationError
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.serializers.contracts import ACCOUNTS_ERROR_RESPONSES
from accounts.serializers.onboarding import (
    ActivationGoalRequestSerializer,
    ActivationStateQuerySerializer,
    ActivationStateResponseSerializer,
)
from accounts.services.onboarding.activation_state import (
    resolve_activation_state_for_request,
)
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.goals import (
    OnboardingGoalConflict,
    save_onboarding_goal,
)
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class ActivationStateView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        query_serializer=ActivationStateQuerySerializer,
        responses={200: ActivationStateResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    )
    def get(self, request):
        try:
            query_keys = set(ActivationStateQuerySerializer().fields)
            query_data = {
                key: value
                for key, value in request.query_params.items()
                if key in query_keys
            }
            query_serializer = ActivationStateQuerySerializer(data=query_data)
            query_serializer.is_valid(raise_exception=True)

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

    @swagger_auto_schema(
        request_body=ActivationGoalRequestSerializer,
        responses={200: ActivationStateResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
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
