"""
GET /accounts/activate/<uidb64>/<token>/ with a malformed uidb64.

Django's ``urlsafe_base64_decode`` turns a bad-length base64 string into
``ValueError: Invalid base64-encoded string: number of data characters (1)
cannot be 1 more than a multiple of 4``. The activation link must answer that
with a 400 like an invalid token, not a 500.
"""

import pytest
from rest_framework import status


@pytest.mark.integration
@pytest.mark.api
class TestActivateAccountMalformedUid:
    def _inactive_user(self):
        from accounts.models import User

        return User.objects.create_user(
            email="malformed-uid@test.com",
            password="testpassword123",
            name="Malformed Uid",
            is_active=False,
        )

    def test_single_character_uid_returns_bad_request(self, api_client, db):
        """uidb64 "A" (1 data character) is rejected with a 400."""
        from django.core.cache import cache

        cache.clear()
        response = api_client.get("/accounts/activate/A/any-token/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_truncated_real_activation_link_returns_bad_request(
        self, api_client, db
    ):
        """A real activation link whose uid lost characters is a 400 and
        leaves the user inactive."""
        from django.core.cache import cache
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        from accounts.views.signup import account_activation_token

        cache.clear()
        user = self._inactive_user()
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = account_activation_token.make_token(user)
        # Cut the uid to a length that is 1 more than a multiple of 4.
        truncated = uid[: (len(uid) - 1) // 4 * 4 + 1]
        assert len(truncated) % 4 == 1

        response = api_client.get(f"/accounts/activate/{truncated}/{token}/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.is_active is False
