import pytest
from jose import jwt as jose_jwt


def test_saml_sp_config_enforces_signed_responses():
    """Verify SAML SP config in source code uses secure defaults."""
    import inspect

    from saml2_auth.views import _get_saml_client

    source = inspect.getsource(_get_saml_client)

    assert '"allow_unsolicited": False' in source, (
        "allow_unsolicited must be False to reject unsolicited IdP assertions"
    )
    assert '"authn_requests_signed": True' in source, (
        "authn_requests_signed must be True to prevent AuthN request forgery"
    )
    assert '"want_response_signed": True' in source, (
        "want_response_signed must be True to require signed SAML response envelope"
    )
    assert '"want_assertions_signed": True' in source, (
        "want_assertions_signed must be True to require signed assertions"
    )


def test_jwt_rejected_without_signature():
    """A forged token (no signature) should fail verification."""
    forged_token = (
        "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwiZW1haWwiOiJ0ZXN0QGV4YW1wbGUuY29tIn0."
        "forged-signature"
    )

    with pytest.raises(Exception):
        jose_jwt.decode(
            forged_token,
            "fake-key",
            algorithms=["RS256"],
            options={"verify_signature": True},
        )


def test_jwt_signature_verification_is_enforced_in_decode_path():
    """Auth0CallbackView must not disable signature verification."""
    import inspect

    from saml2_auth.views import Auth0CallbackView

    source = inspect.getsource(Auth0CallbackView.get)

    assert (
        'options={"verify_signature": False}' not in source
    ), "Auth0CallbackView.get must not contain verify_signature: False"


def test_auth0_callback_does_not_log_oauth_code():
    """Auth0 callback must not log the OAuth authorization code."""
    import inspect

    from saml2_auth.views import Auth0CallbackView

    source = inspect.getsource(Auth0CallbackView.get)

    assert (
        'logger.info(f"CODE:' not in source
    ), "OAuth authorization code must not be logged"


def test_auth0_callback_does_not_log_token_response():
    """Auth0 callback must not log the full token response body."""
    import inspect

    from saml2_auth.views import Auth0CallbackView

    source = inspect.getsource(Auth0CallbackView.get)

    assert (
        'logger.info(f"RESPONSE JSON:' not in source
    ), "OAuth token response body must not be logged"


def test_auth0_callback_does_not_log_decoded_jwt():
    """Auth0 callback must not log the decoded JWT claims."""
    import inspect

    from saml2_auth.views import Auth0CallbackView

    source = inspect.getsource(Auth0CallbackView.get)

    assert (
        'logger.info(f"DECODED:' not in source
    ), "Decoded JWT claims must not be logged"
