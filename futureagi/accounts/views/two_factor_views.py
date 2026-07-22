import json

import structlog
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from webauthn.helpers import base64url_to_bytes

from accounts.models.user import User
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AccountsEmptyRequestSerializer,
    AccountsTokenPairResponseSerializer,
    OrgTwoFactorPolicyResponseSerializer,
    PasskeyOptionsResponseSerializer,
    RecoveryCodesRegenerateResponseSerializer,
    RecoveryCodesRemainingResponseSerializer,
    TOTPConfirmResponseSerializer,
    TOTPDisableResponseSerializer,
    TOTPSetupResponseSerializer,
    TwoFactorPasskeyVerifyRequestSerializer,
)
from accounts.serializers.two_factor import (
    OrgTwoFactorPolicySerializer,
    RecoveryCodesRegenerateSerializer,
    TOTPConfirmSerializer,
    TOTPDisableSerializer,
    TwoFactorChallengeTokenSerializer,
    TwoFactorStatusSerializer,
    TwoFactorVerifySerializer,
)
from accounts.services.recovery_service import (
    generate_recovery_codes,
    get_remaining_count,
    verify_recovery_code,
)
from accounts.services.token_service import issue_tokens
from accounts.services.totp_service import (
    confirm_totp_device,
    create_totp_device,
    disable_totp,
    generate_qr_code_base64,
    verify_totp_code,
)
from accounts.services.two_factor_challenge import (
    consume_challenge,
    validate_challenge,
)
from accounts.services.webauthn_service import (
    get_authentication_options,
    verify_authentication,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# Keyed the same as PasskeyAuthenticateOptionsView stores it; one-time challenge
# for the WebAuthn assertion, resolved via ``session_id`` from the client.
WEBAUTHN_AUTH_CHALLENGE_KEY = "webauthn_auth_challenge:{}"


class TOTPRateThrottle(UserRateThrottle):
    scope = "totp"
    rate = "5/min"


class TwoFactorStatusView(APIView):
    """GET /accounts/2fa/status/ - Current 2FA status."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        responses={200: TwoFactorStatusSerializer, **ACCOUNTS_ERROR_RESPONSES}
    )
    def get(self, request):
        # Re-fetch user from DB to avoid stale cached reverse relations
        user = User.objects.get(pk=request.user.pk)
        try:
            has_totp = user.totp_device.confirmed
            totp_confirmed_at = user.totp_device.created_at
        except Exception:
            has_totp = False
            totp_confirmed_at = None

        passkey_count = user.webauthn_credentials.count()

        recovery_remaining = None
        if user.has_2fa_enabled:
            recovery_remaining = get_remaining_count(user)

        return Response(
            {
                "two_factor_enabled": user.has_2fa_enabled,
                "methods": {
                    "totp": {
                        "enabled": has_totp,
                        "confirmed_at": totp_confirmed_at,
                    },
                    "passkey": {
                        "enabled": passkey_count > 0,
                        "count": passkey_count,
                    },
                },
                "recovery_codes_remaining": recovery_remaining,
            }
        )


class TOTPSetupView(APIView):
    """POST /accounts/2fa/totp/setup/ - Begin TOTP setup."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [TOTPRateThrottle]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=AccountsEmptyRequestSerializer,
        responses={200: TOTPSetupResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            device, provisioning_uri, secret = create_totp_device(request.user)
        except ValueError as e:
            return self._gm.bad_request(str(e))
        qr_code = generate_qr_code_base64(provisioning_uri)

        return Response(
            {
                "qr_code": qr_code,
                "secret": secret,
                "provisioning_uri": provisioning_uri,
            }
        )


class TOTPConfirmView(APIView):
    """POST /accounts/2fa/totp/confirm/ - Confirm TOTP with code."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [TOTPRateThrottle]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TOTPConfirmSerializer,
        responses={200: TOTPConfirmResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        code = request.validated_data["code"]
        if not confirm_totp_device(request.user, code):
            return self._gm.bad_request("Invalid code. Please try again.")

        # Generate recovery codes
        recovery_codes = generate_recovery_codes(request.user)

        return Response(
            {
                "success": True,
                "recovery_codes": recovery_codes,
            }
        )


class TOTPDisableView(APIView):
    """DELETE /accounts/2fa/totp/ - Disable TOTP."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TOTPDisableSerializer,
        responses={200: TOTPDisableResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def delete(self, request):
        code = request.validated_data["code"]

        # Verify with TOTP or recovery code
        if not (
            verify_totp_code(request.user, code)
            or verify_recovery_code(request.user, code)
        ):
            return self._gm.bad_request(
                "Invalid authentication code. Please try again."
            )

        disable_totp(request.user)
        return Response({"success": True})


class TwoFactorVerifyTOTPView(APIView):
    """POST /accounts/2fa/verify/totp/ - Verify TOTP during login (Phase 2)."""

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TwoFactorVerifySerializer,
        responses={
            200: AccountsTokenPairResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        challenge_id = str(request.validated_data["challenge_token"])
        code = request.validated_data["code"]

        # Validate challenge
        challenge_data = validate_challenge(challenge_id)
        if not challenge_data:
            return self._gm.bad_request("Invalid or expired verification session.")

        # Get the user
        try:
            user = User.objects.get(id=challenge_data["user_id"])
        except User.DoesNotExist:
            return self._gm.bad_request("Invalid verification session.")

        # Verify TOTP code
        if not verify_totp_code(user, code):
            return self._gm.bad_request("Invalid code. Please try again.")

        # Consume challenge and issue tokens
        consume_challenge(challenge_id)
        tokens = issue_tokens(user)

        return Response(tokens)


class TwoFactorVerifyRecoveryView(APIView):
    """POST /accounts/2fa/verify/recovery/ - Verify recovery code during login."""

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TwoFactorVerifySerializer,
        responses={
            200: AccountsTokenPairResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        challenge_id = str(request.validated_data["challenge_token"])
        code = request.validated_data["code"]

        challenge_data = validate_challenge(challenge_id)
        if not challenge_data:
            return self._gm.bad_request("Invalid or expired verification session.")

        try:
            user = User.objects.get(id=challenge_data["user_id"])
        except User.DoesNotExist:
            return self._gm.bad_request("Invalid verification session.")

        if not verify_recovery_code(user, code):
            return self._gm.bad_request("Invalid or already used recovery code.")

        consume_challenge(challenge_id)
        tokens = issue_tokens(user)

        # Add warning if low on recovery codes
        remaining = get_remaining_count(user)
        response_data = {**tokens}
        if remaining <= 2:
            response_data["recovery_codes_warning"] = (
                f"You have {remaining} recovery codes remaining."
            )

        return Response(response_data)


class TwoFactorVerifyPasskeyOptionsView(APIView):
    """POST /accounts/2fa/verify/passkey/options/ - Get WebAuthn options for passkey as 2FA."""

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TwoFactorChallengeTokenSerializer,
        responses={200: PasskeyOptionsResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        challenge_id = str(request.validated_data["challenge_token"])

        challenge_data = validate_challenge(challenge_id, count_attempt=False)
        if not challenge_data:
            return self._gm.bad_request("Invalid or expired verification session.")

        try:
            user = User.objects.get(id=challenge_data["user_id"])
        except User.DoesNotExist:
            return self._gm.bad_request("Invalid verification session.")

        options_json, _ = get_authentication_options(user=user)

        return Response(options_json)


class TwoFactorVerifyPasskeyView(APIView):
    """POST /accounts/2fa/verify/passkey/ - Verify passkey as 2FA during login."""

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=TwoFactorPasskeyVerifyRequestSerializer,
        responses={
            200: AccountsTokenPairResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        challenge_id = str(request.validated_data["challenge_token"])
        credential_response = request.validated_data["credential"]

        challenge_data = validate_challenge(challenge_id)
        if not challenge_data:
            return self._gm.bad_request("Invalid or expired verification session.")

        try:
            user = User.objects.get(id=challenge_data["user_id"])
        except User.DoesNotExist:
            return self._gm.bad_request("Invalid verification session.")

        # DRF JSONField(binary=False) returns JSON-encoded strings unchanged;
        # the browser's WebAuthn ceremony result is stringified on the client,
        # so parse it here before handing it to verify_authentication.
        if isinstance(credential_response, str):
            try:
                credential_response = json.loads(credential_response)
            except (json.JSONDecodeError, TypeError):
                return self._gm.bad_request("Invalid credential data.")

        # ``session_id`` keys the one-time WebAuthn challenge in Redis.
        # Clients send it at the top level; the ``_session_id`` fallback
        # inside ``credential`` exists for an older client shape.
        session_id = request.validated_data.get(
            "session_id", ""
        ) or credential_response.pop("_session_id", "")
        challenge_cache_key = WEBAUTHN_AUTH_CHALLENGE_KEY.format(session_id)
        raw_challenge_data = cache.get(challenge_cache_key)
        if not raw_challenge_data:
            return self._gm.bad_request("WebAuthn challenge expired.")

        # Invalidate the challenge the moment we read it — matches the
        # one-time-use contract regardless of whether verification below
        # succeeds, and narrows the replay window to a single request.
        cache.delete(challenge_cache_key)

        try:
            webauthn_challenge_data = json.loads(raw_challenge_data)
            expected_challenge = base64url_to_bytes(
                webauthn_challenge_data["challenge"]
            )
            verify_authentication(credential_response, expected_challenge, user=user)
        except Exception as e:
            logger.exception("passkey_2fa_verification_failed", error=str(e))
            return self._gm.bad_request("Passkey verification failed.")

        consume_challenge(challenge_id)
        tokens = issue_tokens(user)

        return Response(tokens)


class RecoveryCodesView(APIView):
    """GET /accounts/2fa/recovery-codes/ - Get remaining count."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: RecoveryCodesRemainingResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        remaining = get_remaining_count(request.user)
        return Response({"remaining": remaining})


class RecoveryCodesRegenerateView(APIView):
    """POST /accounts/2fa/recovery-codes/regenerate/ - Generate new codes."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=RecoveryCodesRegenerateSerializer,
        responses={
            200: RecoveryCodesRegenerateResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        user = request.user
        has_totp = False
        try:
            has_totp = user.totp_device.confirmed
        except ObjectDoesNotExist:
            pass
        has_passkey = user.webauthn_credentials.exists()

        code = request.validated_data.get("code")
        password = request.validated_data.get("password")

        if has_totp:
            # User has TOTP — require code verification
            if not code:
                return self._gm.bad_request(
                    "Authenticator or recovery code is required."
                )
            if not (verify_totp_code(user, code) or verify_recovery_code(user, code)):
                return self._gm.bad_request("Invalid code.")
        elif has_passkey:
            # Passkey-only user — require password verification
            if not password:
                return self._gm.bad_request(
                    "Password is required to regenerate recovery codes."
                )
            if not user.check_password(password):
                return self._gm.bad_request("Invalid password.")
        else:
            return self._gm.bad_request(
                "Enable a two-factor authentication method before generating recovery codes."
            )

        recovery_codes = generate_recovery_codes(user)
        return Response({"recovery_codes": recovery_codes})


class OrgTwoFactorPolicyView(APIView):
    """GET/PUT /accounts/organization/2fa-policy/ - Org 2FA policy.

    GET is available to all authenticated members (read policy).
    PUT is admin-gated inline (Level.ADMIN+) rather than via a permission
    class so that a single view can serve both roles without splitting.
    """

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        responses={
            200: OrgTwoFactorPolicyResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        org = getattr(request, "organization", None)
        if not org:
            return self._gm.bad_request("No organization context.")

        return Response(
            {
                "require_2fa": org.require_2fa,
                "require_2fa_grace_period_days": org.require_2fa_grace_period_days,
                "require_2fa_enforced_at": org.require_2fa_enforced_at,
            }
        )

    @validated_request(
        request_serializer=OrgTwoFactorPolicySerializer,
        responses={
            200: OrgTwoFactorPolicyResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def put(self, request):
        org = getattr(request, "organization", None)
        if not org:
            return self._gm.bad_request("No organization context.")

        # Check admin permissions using integer level system
        from tfc.constants.levels import Level

        user_level = request.user.get_membership_level(org)
        if user_level is None or user_level < Level.ADMIN:
            return self._gm.forbidden_response(
                "Only organization owners and admins can update 2FA policy."
            )

        require_2fa = request.validated_data["require_2fa"]

        # Cannot enforce 2FA for the org unless your own 2FA is enabled
        if require_2fa and not request.user.has_2fa_enabled:
            return self._gm.bad_request(
                "You must enable two-factor authentication on your own account before requiring it for the organization."
            )
        grace_days = request.validated_data.get("require_2fa_grace_period_days")

        update_fields = ["require_2fa"]

        if require_2fa and not org.require_2fa:
            # Newly enabling — set enforcement timestamp
            org.require_2fa_enforced_at = timezone.now()
            update_fields.append("require_2fa_enforced_at")
        elif not require_2fa and org.require_2fa_enforced_at is not None:
            org.require_2fa_enforced_at = None
            update_fields.append("require_2fa_enforced_at")

        org.require_2fa = require_2fa

        if grace_days is not None:
            org.require_2fa_grace_period_days = grace_days
            update_fields.append("require_2fa_grace_period_days")

        org.save(update_fields=update_fields)

        return Response(
            {
                "require_2fa": org.require_2fa,
                "require_2fa_grace_period_days": org.require_2fa_grace_period_days,
                "require_2fa_enforced_at": org.require_2fa_enforced_at,
            }
        )
