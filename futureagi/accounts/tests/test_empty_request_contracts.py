from types import SimpleNamespace
from uuid import uuid4

from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.views.two_factor_views import TOTPSetupView
from accounts.views.webauthn_views import (
    PasskeyAuthenticateOptionsView,
    PasskeyRegisterOptionsView,
)


def _user():
    user_id = uuid4()
    return SimpleNamespace(
        id=user_id,
        pk=user_id,
        is_authenticated=True,
    )


def _assert_rejects_non_empty_body(response):
    assert response.status_code == 400
    assert response.data["status"] is False
    assert response.data["details"] == {
        "non_field_errors": ["This endpoint does not accept a request body."],
        "unexpected": ["Unknown field."],
    }


def test_passkey_register_options_rejects_non_empty_body():
    factory = APIRequestFactory()
    request = factory.post("/", {"unexpected": "value"}, format="json")
    force_authenticate(request, user=_user())

    response = PasskeyRegisterOptionsView.as_view()(request)

    _assert_rejects_non_empty_body(response)


def test_passkey_authenticate_options_rejects_non_empty_body():
    factory = APIRequestFactory()
    request = factory.post("/", {"unexpected": "value"}, format="json")

    response = PasskeyAuthenticateOptionsView.as_view()(request)

    _assert_rejects_non_empty_body(response)


def test_totp_setup_rejects_non_empty_body():
    factory = APIRequestFactory()
    request = factory.post("/", {"unexpected": "value"}, format="json")
    force_authenticate(request, user=_user())

    response = TOTPSetupView.as_view()(request)

    _assert_rejects_non_empty_body(response)
