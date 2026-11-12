import urllib.parse

import structlog
from accounts.aws_marketplace_utils import (
    get_onboarding_session,
    onboard_aws_customer,
    process_aws_launch_software,
    process_aws_onboarding,
)
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AWSMarketplaceSignupRequestSerializer,
    AWSMarketplaceSignupResponseSerializer,
    AWSOnboardingExchangeRequestSerializer,
    AWSOnboardingExchangeResponseSerializer,
)
from accounts.services.aws_marketplace import AWSMarketplaceService
from accounts.services.aws_onboarding_attempt import (
    OnboardingAttemptBusy,
    issue_attempt,
    use_attempt,
)
from accounts.views.handoff_exchange import (
    HandoffRedeemView,
    HandoffRefusedResponseSerializer,
    _app_origin,
    _refused,
)
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils.functional import SimpleLazyObject
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, parser_classes
from rest_framework.exceptions import ParseError, UnsupportedMediaType
from rest_framework.response import Response
from rest_framework.parsers import FormParser
from tfc.utils.api_contracts import validated_api_request, validated_request
from tfc.utils.general_methods import GeneralMethods

from accounts.services.handoff_exchange import (
    KIND_AWS_MARKETPLACE,
    KIND_AWS_ONBOARDING,
    HandoffRefused,
    issue,
    redeem,
    redeem_with_deadline,
    restore,
)

logger = structlog.get_logger(__name__)

# Sign-up refusals the register view acts on. Any other failure keeps the
# categorical "Signup failed" and never exception text.
SIGNUP_EMAIL_TAKEN = "An account with this email already exists"
SIGNUP_EMAIL_TAKEN_MESSAGE = (
    "An account with this email already exists. "
    "Sign in instead, or use a different email."
)
SIGNUP_TERMINAL_REASONS = frozenset(
    {
        "Invalid or expired onboarding token",
        "This AWS Marketplace customer already has an account",
    }
)

_gm = GeneralMethods()
# Lazy: building boto3 clients at import costs ~8 MB RSS in every process
# and only AWS Marketplace deployments use them.
aws_marketplace_service = SimpleLazyObject(AWSMarketplaceService)
APP_URL = (
    f"https://{settings.APP_URL}"
    if settings.APP_URL
    else settings.BASE_URL.replace("http://", "https://")
)


class AWSOnboardingExchangeView(HandoffRedeemView):
    """Exchange on arrival, not after the buyer has filled in the form."""

    def handle_exception(self, exc):
        # Contract validation parses before post(); keep parser failures categorical.
        if isinstance(exc, (ParseError, UnsupportedMediaType)):
            return _refused()
        return super().handle_exception(exc)

    @validated_request(
        request_serializer=AWSOnboardingExchangeRequestSerializer,
        responses={
            200: AWSOnboardingExchangeResponseSerializer,
            400: HandoffRefusedResponseSerializer,
        },
        reject_unknown_fields=True,
        validation_error_response=lambda _errors: _refused(),
        security=[],
    )
    def post(self, request):
        origin = _app_origin()
        if origin is None or request.headers.get("Origin") != origin:
            return _refused()
        try:
            data = request.data
            if not isinstance(data, dict) or set(data) != {"code"}:
                return _refused()
            token = redeem(data["code"], KIND_AWS_ONBOARDING)["onboarding_token"]
            get_onboarding_session(token)
            attempt = issue_attempt(token)
        except (HandoffRefused, ValueError, ParseError, UnsupportedMediaType):
            return _refused()
        return Response({"onboarding_attempt": attempt})


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

        except Exception:
            return _gm.bad_request("Invalid form data")

        if not registration_token:
            return _gm.bad_request("Missing AWS Marketplace registration token")

        logger.info("AWS Marketplace token verification requested")

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
            code = issue(KIND_AWS_ONBOARDING, {"onboarding_token": onboarding_token})
            # Keep the signup reader's parameter name, but its value is now an
            # atomic, <=60-second exchange code, not the reusable capability.
            redirect_url = f"{APP_URL}/auth/jwt/register?onboarding_token={code}"
        else:
            redirect_url = f"{APP_URL}/auth/jwt/login?returnTo=%2Fdashboard%2Fdevelop"

        response = HttpResponseRedirect(redirect_url)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    except Exception:
        return _gm.bad_request("Token verification failed")


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
        capability = data["onboarding_token"]
        email = data["email"]
        full_name = data["full_name"]

        if capability.startswith("aws1_"):
            terminal_reason = None
            with use_attempt(capability) as onboarding_token:
                try:
                    get_onboarding_session(onboarding_token)
                except ValueError as exc:
                    raise HandoffRefused() from exc
                try:
                    user_email = process_aws_onboarding(
                        onboarding_token, email, full_name
                    )
                except ValueError as exc:
                    if str(exc) not in SIGNUP_TERMINAL_REASONS:
                        # An email that already has an account (or any other
                        # failure) keeps this form attempt for a retry.
                        raise
                    # Nothing a new email could fix: leaving the block
                    # normally spends the attempt.
                    terminal_reason = str(exc)
            if terminal_reason is not None:
                return _gm.bad_request(terminal_reason)
        else:
            # Compatibility for already-open clients: a redirect code still
            # redeems once. Raw purchase capabilities remain refused.
            payload, expires_at = redeem_with_deadline(capability, KIND_AWS_ONBOARDING)
            try:
                user_email = process_aws_onboarding(
                    payload["onboarding_token"], email, full_name
                )
            except ValueError as exc:
                if str(exc) == SIGNUP_EMAIL_TAKEN:
                    # No account was created, so the buyer keeps this link for
                    # a corrected email, inside its original deadline.
                    restore(capability, KIND_AWS_ONBOARDING, payload, expires_at)
                raise

        response_data = {
            "message": "Account created successfully",
            "user_email": user_email,
        }

        return _gm.success_response(response_data)

    except OnboardingAttemptBusy:
        return _gm.bad_request("Signup is already in progress. Please try again shortly.")
    except HandoffRefused:
        # Fixed terminal category already recognized by the registration reader.
        # Keep HTTP 400 and the existing bad_request envelope, never exception text.
        return _gm.bad_request("Invalid or expired onboarding token")
    except ValueError as exc:
        # A fixed, actionable validation category, never arbitrary helper text.
        reason = str(exc)
        if reason == SIGNUP_EMAIL_TAKEN:
            return _gm.bad_request(SIGNUP_EMAIL_TAKEN_MESSAGE)
        if reason in SIGNUP_TERMINAL_REASONS:
            return _gm.bad_request(reason)
        return _gm.bad_request("Signup failed")
    except Exception:
        return _gm.bad_request("Signup failed")


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

    Resolve the AWS Marketplace customer and redirect to signup or real sign-in.
    The subscription token does not authenticate a person.
    """
    try:
        registration_token = None
        try:
            body_str = request.body.decode("utf-8")
            parsed = urllib.parse.parse_qs(body_str)
            registration_token = parsed.get("x-amzn-marketplace-token", [None])[0]
        except Exception:
            return _gm.bad_request("Invalid or expired AWS Marketplace token")

        if not registration_token:
            return _gm.bad_request("Missing AWS Marketplace registration token")

        logger.info("AWS Marketplace launch software requested")

        response_data, error_message = process_aws_launch_software(registration_token)

        if error_message:
            # Helper failures can contain exception text; expose only a category.
            return _gm.bad_request("Launch failed")

        if response_data and response_data.get("action") == "login":
            redirect_url = f"{APP_URL}/auth/jwt/login?returnTo=%2Fdashboard%2Fdevelop"
            response = HttpResponseRedirect(redirect_url)
            response["Cache-Control"] = "no-store"
            response["Referrer-Policy"] = "no-referrer"
            return response

        if response_data and response_data.get("action") == "signup":
            code = issue(
                KIND_AWS_ONBOARDING,
                {"onboarding_token": response_data.get("onboarding_token")},
            )
            redirect_url = f"{APP_URL}/auth/jwt/register?onboarding_token={code}"
            response = HttpResponseRedirect(redirect_url)
            response["Cache-Control"] = "no-store"
            response["Referrer-Policy"] = "no-referrer"
            return response

        access_token = response_data.get("access")
        refresh_token = response_data.get("refresh")
        new_org = response_data.get("new_org", False)

        dashboard_base = f"{APP_URL}/dashboard/develop"

        code = issue(
            KIND_AWS_MARKETPLACE, {"access": access_token, "refresh": refresh_token}
        )
        query = urllib.parse.urlencode(
            {"code": code, "kind": KIND_AWS_MARKETPLACE, "new_org": new_org}
        )
        redirect_url = f"{dashboard_base}?{query}"

        response = HttpResponseRedirect(redirect_url)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response

    except Exception:
        return _gm.bad_request("Launch failed")
