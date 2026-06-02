import urllib.parse

import structlog
from django.conf import settings
from django.http import HttpResponseRedirect
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser

from accounts.aws_marketplace_utils import (
    onboard_aws_customer,
    process_aws_launch_software,
    process_aws_onboarding,
)
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AWSMarketplaceSignupRequestSerializer,
    AWSMarketplaceSignupResponseSerializer,
)
from accounts.services.aws_marketplace import AWSMarketplaceService
from tfc.utils.api_contracts import validated_api_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_gm = GeneralMethods()
aws_marketplace_service = AWSMarketplaceService()
APP_URL = (
    f"https://{settings.APP_URL}"
    if settings.APP_URL
    else settings.BASE_URL.replace("http://", "https://")
)

AWS_MARKETPLACE_TOKEN_FORM_PARAMS = [
    openapi.Parameter(
        "x-amzn-marketplace-token",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=True,
    ),
    openapi.Parameter(
        "x-amzn-marketplace-product-id",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=False,
    ),
    openapi.Parameter(
        "x-amzn-marketplace-agreement-id",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=False,
    ),
]


@swagger_auto_schema(
    method="post",
    manual_parameters=AWS_MARKETPLACE_TOKEN_FORM_PARAMS,
    responses={
        302: "Redirects to the Future AGI frontend.",
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@api_view(["POST"])
@parser_classes([FormParser])
def aws_marketplace_verify_token(request):
    """
    Verify AWS Marketplace token and redirect to appropriate frontend URL
    This endpoint accepts application/x-www-form-urlencoded.
    """
    try:
        content_type = request.content_type or ""

        if "application/x-www-form-urlencoded" not in content_type:
            return _gm.bad_request(
                "Content-Type must be application/x-www-form-urlencoded"
            )

        # Parse application/x-www-form-urlencoded body
        try:
            body_str = request.body.decode("utf-8")

            parsed_data = urllib.parse.parse_qs(body_str)

            registration_token = parsed_data.get("x-amzn-marketplace-token", [None])[0]
            product_id = parsed_data.get("x-amzn-marketplace-product-id", [None])[0]
            agreement_id = parsed_data.get("x-amzn-marketplace-agreement-id", [None])[0]

        except Exception as parse_error:
            return _gm.bad_request(f"Invalid form data: {str(parse_error)}")

        if not registration_token:
            return _gm.bad_request("Missing AWS Marketplace registration token")

        logger.info(
            f"AWS Marketplace token verification - Token: {registration_token[:20]}..., Product ID: {product_id}, Agreement ID: {agreement_id}"
        )

        try:
            customer_identifier, customer_aws_account_id, product_code = (
                aws_marketplace_service.resolve_customer_token(registration_token)
            )
        except Exception:
            return _gm.bad_request("Invalid or expired AWS Marketplace token")

        onboarding_token, has_user = onboard_aws_customer(
            customer_identifier,
            customer_aws_account_id,
            product_code,
            product_id,
            agreement_id,
        )

        action = "login" if has_user else "signup"

        logger.info(
            f"Token verification successful for customer {customer_identifier}, action: {action}"
        )

        if action == "signup":
            redirect_url = (
                f"{APP_URL}/auth/jwt/register?onboarding_token={onboarding_token}"
            )
        else:
            redirect_url = f"{APP_URL}/auth/jwt/login?returnTo=%2Fdashboard%2Fdevelop"

        return HttpResponseRedirect(redirect_url)

    except Exception as e:
        return _gm.bad_request(f"Token verification failed: {str(e)}")


@swagger_auto_schema(
    method="post",
    request_body=AWSMarketplaceSignupRequestSerializer,
    responses={200: AWSMarketplaceSignupResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    runtime_request_validation=True,
    runtime_response_validation=True,
)
@api_view(["POST"])
@validated_api_request(
    request_serializer=AWSMarketplaceSignupRequestSerializer,
    responses={200: AWSMarketplaceSignupResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    reject_unknown_fields=True,
    document=False,
)
def aws_marketplace_signup(request):
    """
    Complete AWS Marketplace customer signup

    This endpoint is called after token verification to create a new user account
    for an AWS Marketplace customer.
    """
    try:
        data = request.validated_data
        onboarding_token = data["onboarding_token"]
        email = data["email"]
        full_name = data["full_name"]

        user_email = process_aws_onboarding(onboarding_token, email, full_name)

        response_data = {
            "message": "Account created successfully",
            "user_email": user_email,
        }

        return _gm.success_response(response_data)

    except Exception as e:
        return _gm.bad_request(f"Signup failed: {str(e)}")


@swagger_auto_schema(
    method="post",
    manual_parameters=AWS_MARKETPLACE_TOKEN_FORM_PARAMS,
    responses={
        200: "Successful requests redirect to the Future AGI frontend.",
        302: "Redirects to the Future AGI frontend.",
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@api_view(["POST"])
@parser_classes([FormParser])
def aws_marketplace_launch_software(request):
    """
    Handle "Launch Software" action from AWS Marketplace

    This endpoint authenticates an existing AWS Marketplace customer
    and redirects appropriately.
    """
    try:
        registration_token = None
        try:
            body_str = request.body.decode("utf-8")
            parsed = urllib.parse.parse_qs(body_str)
            registration_token = parsed.get("x-amzn-marketplace-token", [None])[0]
        except Exception as parse_error:
            return _gm.bad_request(
                f"Invalid or expired AWS Marketplace token: {str(parse_error)}"
            )

        if not registration_token:
            return _gm.bad_request("Missing AWS Marketplace registration token")

        logger.info(
            f"AWS Marketplace launch software - Token: {registration_token[:20]}..."
        )

        response_data, error_message = process_aws_launch_software(registration_token)

        if error_message:
            return _gm.bad_request(error_message)

        if response_data and response_data.get("action") == "signup":
            onboarding_token = response_data.get("onboarding_token")
            redirect_url = (
                f"{APP_URL}/auth/jwt/register?onboarding_token={onboarding_token}"
            )
            return HttpResponseRedirect(redirect_url)

        access_token = response_data.get("access")
        refresh_token = response_data.get("refresh")
        new_org = response_data.get("new_org", False)

        dashboard_base = f"{APP_URL}/dashboard/develop"

        query = urllib.parse.urlencode(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "new_org": new_org,
            }
        )
        redirect_url = f"{dashboard_base}?{query}"

        return HttpResponseRedirect(redirect_url)

    except Exception as e:
        return _gm.bad_request(f"Launch failed: {str(e)}")
