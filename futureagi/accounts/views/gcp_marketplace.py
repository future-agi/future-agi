import urllib.parse

import structlog
from django.conf import settings
from django.http import HttpResponseRedirect
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser

from accounts.gcp_marketplace_utils import (
    onboard_account,
    process_signup,
    verify_marketplace_token,
)
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    GCPMarketplaceSignupRequestSerializer,
    GCPMarketplaceSignupResponseSerializer,
)
from tfc.utils.api_contracts import validated_api_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_gm = GeneralMethods()

APP_URL = (
    f"https://{settings.APP_URL}"
    if settings.APP_URL
    else settings.BASE_URL.replace("http://", "https://")
)

GCP_MARKETPLACE_TOKEN_FORM_PARAMS = [
    openapi.Parameter(
        "x-gcp-marketplace-token",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=True,
    ),
]


@swagger_auto_schema(
    method="post",
    manual_parameters=GCP_MARKETPLACE_TOKEN_FORM_PARAMS,
    responses={
        302: "Redirects to the Future AGI frontend.",
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@api_view(["POST"])
@parser_classes([FormParser])
def gcp_marketplace_verify_token(request):
    """Landing endpoint for the Producer Portal Sign up URL.

    Google posts the signed marketplace token here as form data and expects a
    redirect. Registered in Producer Portal, not called by our own frontend.
    """
    try:
        try:
            body = request.body.decode("utf-8")
            parsed = urllib.parse.parse_qs(body)
            token = parsed.get("x-gcp-marketplace-token", [None])[0]
        except Exception as parse_error:
            return _gm.bad_request(f"Invalid form data: {str(parse_error)}")

        if not token:
            return _gm.bad_request("Missing GCP Marketplace token")

        try:
            account_id, user_identity = verify_marketplace_token(token)
        except Exception:
            logger.warning("gcp_marketplace_token_invalid")
            return _gm.bad_request("Invalid or expired GCP Marketplace token")

        onboarding_token, has_user = onboard_account(account_id, user_identity)

        logger.info(
            "gcp_marketplace_token_verified",
            account_id=account_id,
            action="login" if has_user else "signup",
        )

        if has_user:
            redirect_url = f"{APP_URL}/auth/jwt/login?returnTo=%2Fdashboard%2Fdevelop"
        else:
            redirect_url = (
                f"{APP_URL}/auth/jwt/register?onboarding_gcp_token={onboarding_token}"
            )

        return HttpResponseRedirect(redirect_url)

    except Exception as e:
        logger.exception("gcp_marketplace_verify_token_failed")
        return _gm.bad_request(f"Token verification failed: {str(e)}")


@swagger_auto_schema(
    method="post",
    request_body=GCPMarketplaceSignupRequestSerializer,
    responses={
        200: GCPMarketplaceSignupResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
    runtime_request_validation=True,
    runtime_response_validation=True,
)
@api_view(["POST"])
@validated_api_request(
    request_serializer=GCPMarketplaceSignupRequestSerializer,
    responses={
        200: GCPMarketplaceSignupResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
    reject_unknown_fields=True,
    document=False,
)
def gcp_marketplace_signup(request):
    """Complete sign-up for a GCP Marketplace customer."""
    try:
        data = request.validated_data
        user = process_signup(
            data["onboarding_token"],
            data["email"],
            data["full_name"],
        )
        return _gm.success_response(
            {"message": "Account created successfully", "user_email": user.email}
        )
    except ValueError as e:
        return _gm.bad_request(str(e))
    except Exception as e:
        logger.exception("gcp_marketplace_signup_failed")
        return _gm.bad_request(f"Signup failed: {str(e)}")
