"""GET /accounts/activate/<uidb64>/<token>/ with a uid part that is not a user id."""

import pytest
from django.core.cache import cache
from django.utils.http import urlsafe_base64_encode
from rest_framework import status


@pytest.mark.integration
@pytest.mark.api
class TestActivateAccountUid:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        cache.clear()
        yield
        cache.clear()

    def test_undecodable_uid_returns_invalid_link(self, api_client, db):
        """Non-UTF-8 bytes in the uid part are a bad link, not a server error."""
        uid = urlsafe_base64_encode(b"\xff\xfe\x80")
        response = api_client.get(f"/accounts/activate/{uid}/any-token/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content

    def test_uid_text_that_is_not_a_user_id_returns_invalid_link(self, api_client, db):
        """A uid that decodes to text but is not a user id is a bad link too."""
        uid = urlsafe_base64_encode(b"not-a-user-id")
        response = api_client.get(f"/accounts/activate/{uid}/any-token/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
