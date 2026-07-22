import json

import structlog
from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.webauthn_credential import WebAuthnCredential
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AccountsEmptyRequestSerializer,
    AccountsTokenPairResponseSerializer,
    PasskeyCredentialRequestSerializer,
    PasskeyOptionsResponseSerializer,
    PasskeyRegisterVerifyResponseSerializer,
    PasskeyRenameResponseSerializer,
)
from accounts.serializers.two_factor import (
    PasskeyRegisterVerifySerializer,
    PasskeyRenameSerializer,
    WebAuthnCredentialSerializer,
)
from accounts.services.token_service import issue_tokens
from accounts.services.webauthn_service import (
    get_authentication_options,
    get_registration_options,
    verify_authentication,
    verify_registration,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class PasskeyRegisterOptionsView(APIView):
    """POST /accounts/passkey/register/options/ - Get registration options."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=AccountsEmptyRequestSerializer,
        responses={200: PasskeyOptionsResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            options_json, _ = get_registration_options(request.user)
            return Response(options_json)
        except Exception as e:
            logger.exception("passkey_register_options_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to generate registration options."
            )


class PasskeyRegisterVerifyView(APIView):
    """POST /accounts/passkey/register/verify/ - Verify registration."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=PasskeyRegisterVerifySerializer,
        responses={
            201: PasskeyRegisterVerifyResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        credential_response = request.validated_data["credential"]
        name = request.validated_data.get("name", "")

        try:
            # Retrieve the stored challenge
            challenge_key = f"webauthn_reg_challenge:{request.user.id}"
            stored_challenge = cache.get(challenge_key)
            if not stored_challenge:
                return self._gm.bad_request("Registration challenge expired.")

            from webauthn.helpers import base64url_to_bytes

            expected_challenge = base64url_to_bytes(stored_challenge)

            credential = verify_registration(
                request.user, credential_response, expected_challenge, name=name
            )

            # If this is the user's first 2FA method, generate recovery codes
            from accounts.services.recovery_service import (
                generate_recovery_codes,
                get_remaining_count,
            )

            if get_remaining_count(request.user) == 0:
                recovery_codes = generate_recovery_codes(request.user)
            else:
                recovery_codes = None

            response_data = {
                "id": str(credential.id),
                "name": credential.name,
                "created_at": credential.created_at,
            }
            if recovery_codes:
                response_data["recovery_codes"] = recovery_codes

            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception("passkey_registration_failed", error=str(e))
            return self._gm.bad_request("Failed to register passkey.")


class PasskeyListView(APIView):
    """GET /accounts/passkeys/ - List user's passkeys."""

    permission_classes = [IsAuthenticated]

    @validated_request(
        responses={
            200: WebAuthnCredentialSerializer(many=True),
            **ACCOUNTS_ERROR_RESPONSES,
        }
    )
    def get(self, request):
        passkeys = WebAuthnCredential.objects.filter(user=request.user)
        serializer = WebAuthnCredentialSerializer(passkeys, many=True)
        return Response(serializer.data)


class PasskeyDetailView(APIView):
    """PATCH/DELETE /accounts/passkeys/<uuid:pk>/ - Rename or delete."""

    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=PasskeyRenameSerializer,
        responses={200: PasskeyRenameResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def patch(self, request, pk):
        try:
            passkey = WebAuthnCredential.objects.get(id=pk, user=request.user)
        except WebAuthnCredential.DoesNotExist:
            return self._gm.not_found("Passkey not found.")

        passkey.name = request.validated_data["name"]
        passkey.save(update_fields=["name", "updated_at"])

        return Response({"id": str(passkey.id), "name": passkey.name})

    @validated_request(responses={204: "Passkey deleted.", **ACCOUNTS_ERROR_RESPONSES})
    def delete(self, request, pk):
        try:
            passkey = WebAuthnCredential.objects.get(id=pk, user=request.user)
        except WebAuthnCredential.DoesNotExist:
            return self._gm.not_found("Passkey not found.")

        passkey.delete()

        # If no more 2FA methods, clean up recovery codes
        user = request.user
        if not user.has_2fa_enabled:
            from accounts.models.recovery_code import RecoveryCode

            RecoveryCode.objects.filter(user=user).delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class PasskeyAuthenticateOptionsView(APIView):
    """POST /accounts/passkey/authenticate/options/ - Passwordless auth options."""

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=AccountsEmptyRequestSerializer,
        responses={200: PasskeyOptionsResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request):
        try:
            options_json, _ = get_authentication_options(user=None)
            return Response(options_json)
        except Exception as e:
            logger.exception("passkey_auth_options_failed", error=str(e))
            return self._gm.bad_request(
                "Failed to generate authentication options."
            )


class PasskeyAuthenticateVerifyView(APIView):
    """POST /accounts/passkey/authenticate/verify/ - Passwordless auth verify."""

    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=PasskeyCredentialRequestSerializer,
        responses={
            200: AccountsTokenPairResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def post(self, request):
        credential_response = request.validated_data.get("credential")
        if not credential_response:
            return self._gm.bad_request("Credential data is required.")

        # Handle credential sent as JSON string
        if isinstance(credential_response, str):
            try:
                credential_response = json.loads(credential_response)
            except (json.JSONDecodeError, TypeError):
                return self._gm.bad_request("Invalid credential data.")

        try:
            # Get stored challenge — session_id at top level of request
            session_id = request.validated_data.get(
                "session_id", ""
            ) or credential_response.pop("_session_id", "")
            raw_data = cache.get(f"webauthn_auth_challenge:{session_id}")
            if not raw_data:
                return self._gm.bad_request("Authentication challenge expired.")

            challenge_data = json.loads(raw_data)
            from webauthn.helpers import base64url_to_bytes

            expected_challenge = base64url_to_bytes(challenge_data["challenge"])

            user, credential = verify_authentication(
                credential_response, expected_challenge
            )

            # Clean up challenge
            cache.delete(f"webauthn_auth_challenge:{session_id}")

            # Issue tokens
            tokens = issue_tokens(user)
            return Response(tokens)

        except WebAuthnCredential.DoesNotExist:
            return self._gm.bad_request("Passkey not recognized.")
        except Exception as e:
            logger.warning("passkey_auth_verify_failed", error=str(e))
            return self._gm.bad_request("Passkey verification failed.")
