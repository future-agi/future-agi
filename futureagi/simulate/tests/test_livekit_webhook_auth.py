"""LiveKit webhook: a token with a disallowed ``alg`` is a plain auth failure.

LiveKit signs webhooks with HS256 only. Anyone can POST to the webhook, so a
token whose header names another algorithm must be rejected the same way as
any other bad token, without being logged as an exception (an ERROR-level
record with a traceback, which Sentry turns into a crash event).
"""

import inspect
from unittest.mock import patch

import jwt
from asgiref.sync import async_to_sync
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from simulate.views.livekit_api import LiveKitWebhookView

LIVEKIT_KEY = "test-livekit-key"
LIVEKIT_SECRET = "test-livekit-secret-" + "s" * 64


async def _await_response(response):
    return await response


def _post_webhook(token):
    factory = APIRequestFactory()
    request = factory.post(
        "/simulate/api/livekit/webhook/",
        {"event": "room_started"},
        format="json",
        HTTP_AUTHORIZATION=token,
    )
    with (
        patch("tfc.ee_gates.voice_sim_oss_gate_response", return_value=None),
        patch("simulate.views.livekit_api.logger") as view_logger,
    ):
        response = LiveKitWebhookView.as_view()(request)
        if inspect.isawaitable(response):
            response = async_to_sync(_await_response)(response)
    return response, view_logger


def _claims():
    return {"iss": LIVEKIT_KEY, "sub": "livekit-server", "sha256": "x"}


@override_settings(LIVEKIT_API_KEY=LIVEKIT_KEY, LIVEKIT_API_SECRET=LIVEKIT_SECRET)
def test_webhook_token_with_disallowed_alg_is_rejected_without_logging_a_crash():
    token = jwt.encode(_claims(), LIVEKIT_SECRET, algorithm="HS512")
    assert jwt.get_unverified_header(token)["alg"] == "HS512"

    response, view_logger = _post_webhook(token)

    assert response.status_code == 401
    assert response.data["code"] == "not_authenticated"
    # InvalidAlgorithmError: The specified alg value is not allowed
    assert view_logger.exception.call_count == 0, view_logger.exception.call_args
    assert view_logger.error.call_count == 0, view_logger.error.call_args


@override_settings(LIVEKIT_API_KEY=LIVEKIT_KEY, LIVEKIT_API_SECRET=LIVEKIT_SECRET)
def test_webhook_token_with_alg_none_is_rejected_without_logging_a_crash():
    token = jwt.encode(_claims(), None, algorithm="none")
    assert jwt.get_unverified_header(token)["alg"] == "none"

    response, view_logger = _post_webhook(token)

    assert response.status_code == 401
    assert response.data["code"] == "not_authenticated"
    assert view_logger.exception.call_count == 0, view_logger.exception.call_args
    assert view_logger.error.call_count == 0, view_logger.error.call_args
