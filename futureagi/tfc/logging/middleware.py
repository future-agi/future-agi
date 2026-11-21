"""Request-event formatting for OAuth callbacks and emailed links."""

from django_structlog.middlewares import RequestMiddleware

# Paths whose query carries a credential: SSO callbacks, and the signed
# tokens in notification unsubscribe and snooze links.
_QUERY_DROPPED_PREFIXES = ("/saml2_auth/", "/accounts/notifications/")


class CallbackRequestMiddleware(RequestMiddleware):
    """Request-event formatting for OAuth callbacks and emailed links.

    Drop the entire query on these paths rather than enumerating secret
    names: SSO providers may add code, token, state or error parameters, and
    notification links carry a signed token. Only the log representation
    changes; the views still receive the original request.
    ``format_request`` also covers request_finished and request_failed events.
    """

    @staticmethod
    def format_request(request):
        path = request.get_full_path()
        if request.path_info.startswith(_QUERY_DROPPED_PREFIXES):
            path = path.partition("?")[0]
        return f"{request.method} {path}"
