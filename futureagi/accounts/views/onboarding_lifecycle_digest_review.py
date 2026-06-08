import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.serializers.contracts import ACCOUNTS_ERROR_RESPONSES
from accounts.serializers.onboarding_lifecycle_digest_review import (
    OnboardingLifecycleDigestPreviewQuerySerializer,
    OnboardingLifecycleDigestPromotionRequestSerializer,
    OnboardingLifecycleDigestPromotionResponseSerializer,
    OnboardingLifecycleDigestReviewResponseSerializer,
)
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.lifecycle_digest_promotion import (
    promote_digest_preview_sources,
)
from accounts.services.onboarding.lifecycle_digest_review import (
    digest_preview_review_payload,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class OnboardingLifecycleDigestPreviewReviewView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=OnboardingLifecycleDigestPreviewQuerySerializer,
        responses={
            200: OnboardingLifecycleDigestReviewResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
    )
    def get(self, request):
        context = resolve_onboarding_context(request)
        if not context.organization:
            return self._gm.bad_request("Organization context is required.")
        if not context.permissions["can_manage_workspace"]:
            return self._gm.forbidden_response("Workspace admin access is required.")
        try:
            return self._gm.success_response(
                digest_preview_review_payload(
                    organization=context.organization,
                    workspace=context.workspace,
                    campaign_key=request.validated_query_data.get("campaign_key"),
                    limit=request.validated_query_data.get("limit", 25),
                )
            )
        except Exception as exc:
            logger.exception(
                "onboarding_digest_preview_review_failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
                organization_id=str(getattr(context.organization, "id", "")),
                workspace_id=str(getattr(context.workspace, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to fetch digest preview review"
            )


class OnboardingLifecycleDigestPreviewPromotionView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=OnboardingLifecycleDigestPromotionRequestSerializer,
        responses={
            200: OnboardingLifecycleDigestPromotionResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        strict_request_validation=False,
        reject_unknown_fields=True,
    )
    def post(self, request):
        context = resolve_onboarding_context(request)
        if not context.organization:
            return self._gm.bad_request("Organization context is required.")
        if not context.workspace:
            return self._gm.bad_request("Workspace context is required.")
        if not context.permissions["can_manage_workspace"]:
            return self._gm.forbidden_response("Workspace admin access is required.")
        try:
            data = request.validated_data
            return self._gm.success_response(
                promote_digest_preview_sources(
                    organization=context.organization,
                    workspace=context.workspace,
                    actor=request.user,
                    sources=data["sources"],
                    scope_type=data.get("scope_type", "user"),
                    dry_run=data.get("dry_run", False),
                    reason=data.get("reason", ""),
                )
            )
        except Exception as exc:
            logger.exception(
                "onboarding_digest_preview_promotion_failed",
                error=str(exc),
                user_id=str(getattr(request.user, "id", "")),
                organization_id=str(getattr(context.organization, "id", "")),
                workspace_id=str(getattr(context.workspace, "id", "")),
            )
            return self._gm.internal_server_error_response(
                "Failed to promote digest preview cohort"
            )
