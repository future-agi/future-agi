from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from django.test import RequestFactory

from tfc.telemetry.middleware import OTelContextMiddleware


def test_user_context_does_not_lazy_load_organization():
    """Request enrichment must not dereference an uncached organization FK."""

    class User:
        id = uuid4()
        email = "user@example.com"
        organization_id = uuid4()
        is_authenticated = True
        _state = SimpleNamespace(fields_cache={})

        def get_deferred_fields(self):
            return set()

        @property
        def organization(self):
            raise AssertionError("telemetry middleware lazy-loaded organization")

    user = User()
    request = RequestFactory().get("/api/health/")
    request.user = user
    span = MagicMock()

    OTelContextMiddleware(lambda request: None)._capture_user_context(span, request)

    assert not any(
        call.args[0] == "organization.name"
        for call in span.set_attribute.call_args_list
    )
    assert any(
        call.args == ("organization.id", str(user.organization_id))
        for call in span.set_attribute.call_args_list
    )
