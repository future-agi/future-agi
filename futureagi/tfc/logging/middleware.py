"""Request-event formatting for OAuth callbacks."""

from django_structlog.middlewares import RequestMiddleware


class CallbackRequestMiddleware(RequestMiddleware):
    """Request-event formatting for OAuth callbacks.

    Drop the entire query on the SSO namespace rather than enumerating secret
    names: providers may add code, token, state or error parameters. Only the
    log representation changes; callbacks still receive the original request.
    ``format_request`` also covers request_finished and request_failed events.
    """

    @staticmethod
    def format_request(request):
        path = request.get_full_path()
        if request.path_info.startswith("/saml2_auth/"):
            path = path.partition("?")[0]
        return f"{request.method} {path}"
