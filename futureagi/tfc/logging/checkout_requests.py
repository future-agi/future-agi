"""Redact the checkout session in request-logging path fields before logging.

Source fix, defence in depth for the configured-sink boundaries. The checkout
status route (agentcc/urls_billing.py, checkout/<str:session_id>/) carries a
session in its path, so every request log that names the path is redacted by
route match before the event or record exists:
- django_structlog request_started / request_finished / request_failed: the
  "request" field ("METHOD /full/path?query"), through django_structlog's own
  bind_extra_request_* signals, which receive the log kwargs before logging;
- the Granian access log (granian.access, enabled in production with
  --access-log): the "path" and "query_string" atoms of the record arguments;
- the runserver access log (django.server): the request line argument.
The match is the route's URL shape, not resolution, so request_started (which
runs before URL resolution) and unslashed or malformed paths are covered.
Other routes and fields are returned unchanged.
"""

import logging

from .checkout_encoding import may_be_escaped

_PREFIX = "/agentcc/managed-credits/checkout/"
_ACCESS_LOGGERS = ("granian.access", "django.server")


def redact_request_path(text):
    """text with the checkout route's session segment replaced, else text.

    Literal, percent-encoded (single or double) and JSON-escaped spellings of
    the route prefix are matched. Anything else is returned as the same object.
    """
    if type(text) is not str or (_PREFIX not in text and not may_be_escaped(text)):
        return text
    from .checkout_output import scrub_rendered

    return scrub_rendered(text)


def _redact_log_kwargs(sender, log_kwargs=None, **kwargs):
    if type(log_kwargs) is dict and "request" in log_kwargs:
        log_kwargs["request"] = redact_request_path(log_kwargs["request"])


class AccessPathFilter(logging.Filter):
    """Redact the checkout session in an access log record's path arguments.

    Attached to the access loggers only; every other record is untouched.
    Discover session forms across builtin text arguments before replacing any
    path. Otherwise a query string or another argument loses its correlation
    with the path when a later sink (including handleError) renders the record
    outside request scope. No application object is traversed or evaluated;
    nontext arguments retain identity. Redaction copies the arguments object.
    """

    def filter(self, record):
        from .checkout import find_checkout_values
        from .checkout_output import scrub_rendered

        args = record.args
        if type(args) is dict:
            arguments = args.values()
        elif type(args) is tuple:
            arguments = args
        else:
            return True
        texts = tuple(value for value in arguments if type(value) is str)
        values = find_checkout_values(texts)
        if not values:
            return True

        def redact(value):
            return scrub_rendered(value, values=values) if type(value) is str else value

        if type(args) is dict:
            safe = {key: redact(value) for key, value in args.items()}
            if any(safe[key] is not value for key, value in args.items()):
                record.args = safe
        else:
            safe = tuple(redact(value) for value in args)
            if any(new is not old for new, old in zip(safe, args, strict=True)):
                record.args = safe
        return True


def install_request_path_redaction():
    """Connect the request-logging receivers and access-log filters (idempotent).

    Filters are added to the access loggers directly, never through the
    LOGGING dict: a dictConfig entry for granian.access would replace the
    handler Granian configures for its access log.
    """
    from django_structlog import signals

    for signal in (
        signals.bind_extra_request_metadata,
        signals.bind_extra_request_finished_metadata,
        signals.bind_extra_request_failed_metadata,
    ):
        signal.connect(
            _redact_log_kwargs,
            weak=False,
            dispatch_uid="tfc.logging.checkout_requests.redact_request_path",
        )
    for name in _ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        if not any(type(item) is AccessPathFilter for item in logger.filters):
            logger.addFilter(AccessPathFilter())
