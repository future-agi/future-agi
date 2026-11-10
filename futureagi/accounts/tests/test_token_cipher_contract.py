"""Authentication typing boundaries preserve wire compatibility and fail closed."""

from unittest.mock import patch

import pytest
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import (
    decode_token,
    decrypt_message,
    generate_encrypted_message,
)


@pytest.mark.parametrize(
    "encrypt_key", ["cipher-contract-secret", b"cipher-contract-secret"]
)
@pytest.mark.parametrize(
    "decrypt_key", ["cipher-contract-secret", b"cipher-contract-secret"]
)
def test_text_and_byte_secret_keys_share_the_existing_cipher(
    settings, encrypt_key, decrypt_key
):
    payload = {"user_id": "fixture-user", "id": "fixture-token"}
    settings.SECRET_KEY = encrypt_key
    token = generate_encrypted_message(payload)
    settings.SECRET_KEY = decrypt_key
    assert decrypt_message(token) == payload


@pytest.mark.parametrize("token_fields", [{}, {"id": None}, {"id": ""}])
def test_missing_token_identity_is_rejected_before_cache_or_database(token_fields):
    token = generate_encrypted_message({"user_id": "fixture-user", **token_fields})
    with (
        patch("accounts.authentication.cache.get") as cache_get,
        patch("accounts.authentication.AuthToken.objects.filter") as token_lookup,
        pytest.raises(AuthenticationFailed, match="Invalid auth token"),
    ):
        decode_token(token)
    cache_get.assert_not_called()
    token_lookup.assert_not_called()


@pytest.mark.parametrize("field", ["user_id", "id"])
@pytest.mark.parametrize("value", [None, "", 123, [], {}])
def test_refresh_rejects_invalid_identity_before_cache_or_database(
    api_client, field, value
):
    payload = {"user_id": "fixture-user", "id": "fixture-token", field: value}
    token = generate_encrypted_message(payload)
    with (
        patch("accounts.views.user.verify_recaptcha", return_value=True),
        patch("accounts.views.user.cache.get") as cache_get,
        patch("accounts.views.user.AuthToken.objects.filter") as token_lookup,
        patch("accounts.views.user.User.objects.get") as user_lookup,
    ):
        response = api_client.post(
            "/accounts/token/refresh/", {"refresh": token}, format="json"
        )
    assert response.status_code == 400
    assert response.json()["message"] == "Invalid token."
    cache_get.assert_not_called()
    token_lookup.assert_not_called()
    user_lookup.assert_not_called()


def test_refresh_preserves_valid_string_identity(api_client, user):
    from accounts.models.auth_token import AuthToken, AuthTokenType

    refresh = AuthToken.objects.create(user=user, auth_type=AuthTokenType.REFRESH)
    token = generate_encrypted_message({"user_id": str(user.id), "id": str(refresh.id)})
    with patch("accounts.views.user.verify_recaptcha", return_value=True):
        response = api_client.post(
            "/accounts/token/refresh/", {"refresh": token}, format="json"
        )
    assert response.status_code == 200
    access = decrypt_message(response.json()["access"])
    assert access["user_id"] == str(user.id)
    assert AuthToken.objects.filter(
        id=access["id"], user=user, auth_type=AuthTokenType.ACCESS, is_active=True
    ).exists()
