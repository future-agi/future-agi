"""R19i webhook CRUD/event response sites and credential-bearing transport log."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.http import Http404

from agentcc.tests.r19i_error_support import (
    SENTINEL,
    assert_private,
    block_network,
)
from agentcc.views import webhook_outbound as module


@pytest.mark.parametrize(
    "kind,action",
    [
        ("webhook", "list"),
        ("webhook", "create"),
        ("webhook", "update"),
        ("webhook", "destroy"),
        ("event", "list"),
        ("event", "retry"),
    ],
)
def test_unexpected_webhook_failure_is_private(kind, action):
    cls = (
        module.AgentccWebhookViewSet
        if kind == "webhook"
        else module.AgentccWebhookEventViewSet
    )
    view = cls()
    failure = RuntimeError(SENTINEL)
    request = SimpleNamespace(data={}, organization=SimpleNamespace(id="org"))
    with ExitStack() as stack:
        block_network(stack)
        logger = stack.enter_context(patch.object(module, "logger"))
        if action == "create":
            stack.enter_context(patch.object(module, "AgentccWebhook"))
            stack.enter_context(patch("tfc.ee_gating.check_ee_can_create"))
            stack.enter_context(
                patch.object(module, "AgentccWebhookSerializer", side_effect=failure)
            )
        else:
            seam = "get_queryset" if action == "list" else "get_object"
            stack.enter_context(patch.object(view, seam, side_effect=failure))
        response = getattr(view, action)(request)
    body = assert_private(response, 500)
    assert "Something went wrong. Reference: agentcc_internal_error" in body
    assert response.data["code"] == "agentcc_internal_error"
    logger.exception.assert_not_called()
    logger.warning.assert_called_once()
    assert SENTINEL not in repr(logger.mock_calls)


def test_webhook_not_found_is_still_a_caller_error():
    view = module.AgentccWebhookViewSet()
    with (
        patch.object(view, "get_object", side_effect=Http404(SENTINEL)),
        patch.object(module, "logger"),
    ):
        response = view.destroy(SimpleNamespace())
    assert "not found" in assert_private(response, 404).lower()


def test_receiver_header_value_never_enters_log_response_or_event():
    view = module.AgentccWebhookViewSet()
    instance = SimpleNamespace(
        id="hook", organization=object(), secret="", url="https://example.com/hook"
    )
    session = MagicMock()
    session.post.side_effect = requests.exceptions.InvalidHeader(SENTINEL)
    with ExitStack() as stack:
        block_network(stack)
        stack.enter_context(patch.object(view, "get_object", return_value=instance))
        stack.enter_context(
            patch.object(module, "signed_request", return_value=("{}", {}))
        )
        stack.enter_context(patch.object(module, "ensure_public_http_url"))
        stack.enter_context(
            patch.object(module, "build_ssrf_safe_session", return_value=session)
        )
        events = stack.enter_context(patch.object(module, "AgentccWebhookEvent"))
        logger = stack.enter_context(patch.object(module, "logger"))
        response = view.test(SimpleNamespace())
    assert_private(response, 200)
    assert SENTINEL not in repr(logger.mock_calls)
    assert SENTINEL not in repr(events.objects.create.call_args)
    assert logger.warning.call_args.kwargs["error_type"] == "InvalidHeader"
    session.close.assert_called_once()
