from accounts.serializers.contracts import (
    AccountsEmptyRequestSerializer,
    AccountsErrorResponseSerializer,
    AccountsTokenPairResponseSerializer,
    OrgTwoFactorPolicyResponseSerializer,
    PasskeyOptionsResponseSerializer,
    RecoveryCodesRemainingResponseSerializer,
    TOTPSetupResponseSerializer,
)
from tfc.utils.api_errors import build_error_envelope


def test_accounts_error_serializer_accepts_common_error_envelope():
    serializer = AccountsErrorResponseSerializer(
        data=build_error_envelope({"email": ["This field is required."]})
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["status"] is False
    assert serializer.validated_data["attr"] == "email"
    assert serializer.validated_data["details"] == {
        "email": ["This field is required."]
    }


def test_accounts_error_serializer_accepts_structured_login_result():
    serializer = AccountsErrorResponseSerializer(
        data={
            "status": False,
            "code": "LOGIN_INVALID_CREDENTIALS",
            "detail": "Invalid credentials",
            "result": {
                "error": "Invalid credentials",
                "error_code": "LOGIN_INVALID_CREDENTIALS",
                "remaining_attempts": 4,
            },
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["result"]["error_code"] == (
        "LOGIN_INVALID_CREDENTIALS"
    )
    assert serializer.validated_data["result"]["remaining_attempts"] == 4


def test_accounts_empty_request_serializer_rejects_non_empty_body():
    serializer = AccountsEmptyRequestSerializer(data={"unexpected": True})

    assert not serializer.is_valid()
    assert serializer.errors == {
        "non_field_errors": ["This endpoint does not accept a request body."]
    }


def test_passkey_options_response_serializer_matches_raw_webauthn_shape():
    serializer = PasskeyOptionsResponseSerializer(
        data={
            "challenge": "challenge-token",
            "timeout": 60000,
            "rp": {"name": "Future AGI", "id": "localhost"},
            "user": {
                "id": "user-handle",
                "name": "kartik.nvj@futureagi.com",
                "displayName": "Kartik",
            },
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
            "excludeCredentials": [],
            "authenticatorSelection": {"userVerification": "preferred"},
            "attestation": "none",
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_two_factor_setup_response_serializer_matches_raw_setup_shape():
    serializer = TOTPSetupResponseSerializer(
        data={
            "qr_code": "data:image/png;base64,abc",
            "secret": "JBSWY3DPEHPK3PXP",
            "provisioning_uri": "otpauth://totp/FutureAGI:test@example.com",
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_account_token_response_serializer_matches_login_and_2fa_verify_shape():
    serializer = AccountsTokenPairResponseSerializer(
        data={"access": "access-token", "refresh": "refresh-token"}
    )

    assert serializer.is_valid(), serializer.errors


def test_two_factor_read_response_serializers_match_raw_shapes():
    recovery_serializer = RecoveryCodesRemainingResponseSerializer(
        data={"remaining": 8}
    )
    policy_serializer = OrgTwoFactorPolicyResponseSerializer(
        data={
            "require_2fa": True,
            "require_2fa_grace_period_days": 7,
            "require_2fa_enforced_at": None,
        }
    )

    assert recovery_serializer.is_valid(), recovery_serializer.errors
    assert policy_serializer.is_valid(), policy_serializer.errors
