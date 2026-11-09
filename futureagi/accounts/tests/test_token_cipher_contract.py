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
