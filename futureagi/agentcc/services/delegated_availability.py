"""Availability answers shared by the console's delegated operations.

A delegated call (Try it / Guardrail test, or a batch submission) is offered
only when both hold:

- this deployment's switch is on. AGENTCC_DELEGATED_PLAYGROUND_ENABLED and
  AGENTCC_DELEGATED_BATCH_ENABLED are on when unset; an explicit false turns
  one off (retirement needs both off);
- the gateway says it serves the call. Its GET /-/config reports
  ``"delegated": {"playground": bool, "batch": bool, "cluster": bool}``. An
  older gateway omits the block, and then it serves neither.
"""

import os

import structlog
from django.conf import settings
from django.core.cache import cache
from rest_framework.response import Response

from tfc.utils.api_errors import build_error_envelope

logger = structlog.get_logger(__name__)

# One gateway per deployment, so one cache entry for its report. An empty dict
# stands for "no usable report" so that answer is cached too.
REPORT_CACHE_KEY = "agentcc:gateway_delegated_report:v1"
REPORT_CACHE_SECONDS = 30
_NO_REPORT = {}
_REPORT_FIELDS = ("playground", "batch", "cluster")

BATCH_SINGLE_REPLICA_MESSAGE = (
    "Batch jobs need a single gateway replica, or a cluster whose replicas share a Redis store, in this version."
)


def delegated_enabled(*, batch=False):
    name = (
        "AGENTCC_DELEGATED_BATCH_ENABLED"
        if batch
        else "AGENTCC_DELEGATED_PLAYGROUND_ENABLED"
    )
    # On unless the operator turns it off. A settings value (bool or string)
    # takes precedence over the environment; only True or "true" is on.
    value = getattr(settings, name, os.environ.get(name, "true"))
    return value is True or value == "true"


CONSOLE_RUN_SWITCH = "AGENTCC_DELEGATED_CONSOLE_RUN_ENABLED"


def console_run_enabled():
    """The Console's delegated model calls: on unless set to ``false`` or ``0``
    (the owner's default). They stay inert until an operator registers an
    agent service key (agentcc_console_run_signer_keys)."""
    value = getattr(
        settings, CONSOLE_RUN_SWITCH, os.environ.get(CONSOLE_RUN_SWITCH, "true")
    )
    if value is False:
        return False
    return not (isinstance(value, str) and value.strip().lower() in ("false", "0"))


def _delegated_block(block):
    """The three report fields when ``block`` is a well-formed report, else None."""
    if not isinstance(block, dict):
        return None
    if not all(isinstance(block.get(field), bool) for field in _REPORT_FIELDS):
        return None
    return {field: block[field] for field in _REPORT_FIELDS}


def _cached_report():
    """(hit, report) from the cache; a cache failure is a miss."""
    try:
        cached = cache.get(REPORT_CACHE_KEY)
    except Exception as exc:
        logger.warning(
            "gateway_delegated_report_cache_read_failed",
            error_type=type(exc).__name__,
        )
        return False, None
    if cached == _NO_REPORT:
        return True, None
    report = _delegated_block(cached)
    return (report is not None), report


def gateway_delegated_report(client=None):
    """What the gateway says it serves, from GET /-/config's "delegated" block.

    Returns ``{"playground": bool, "batch": bool, "cluster": bool}``, or None
    when the gateway does not report it (an older gateway), answers something
    else, or cannot be read. The answer, None included, is cached for
    REPORT_CACHE_SECONDS. Never raises. ``client`` is built only on a miss.
    """
    hit, report = _cached_report()
    if hit:
        return report
    try:
        if client is None:
            from agentcc.services.gateway_client import get_gateway_client

            client = get_gateway_client()
        reported = client.get_config()
        report = _delegated_block(
            reported.get("delegated") if isinstance(reported, dict) else None
        )
    except Exception as exc:
        # GatewayClientError, ValueError, transport/TLS errors alike. Only the
        # type is logged: upstream text stays out of the logs.
        report = None
        logger.warning(
            "gateway_delegated_report_failed", error_type=type(exc).__name__
        )
    try:
        cache.set(
            REPORT_CACHE_KEY,
            _NO_REPORT if report is None else report,
            timeout=REPORT_CACHE_SECONDS,
        )
    except Exception as exc:
        logger.warning(
            "gateway_delegated_report_cache_write_failed",
            error_type=type(exc).__name__,
        )
    return report


def delegated_available(*, report, batch=False):
    """The switch is on and the gateway's report says it serves this call.

    ``report`` is gateway_delegated_report()'s answer; None (an older or
    unreadable gateway) means unavailable.
    """
    if not delegated_enabled(batch=batch):
        return False
    if report is None:
        return False
    return report.get("batch" if batch else "playground") is True


def batch_needs_single_replica(report):
    """Batch is on here, but the gateway runs as a cluster and so refuses it."""
    return (
        delegated_enabled(batch=True)
        and report is not None
        and report.get("cluster") is True
        and report.get("batch") is False
    )


def unavailable_response(*, batch=False, report=None):
    if batch and batch_needs_single_replica(report):
        message = BATCH_SINGLE_REPLICA_MESSAGE
    else:
        surface, verb = (
            ("Batch submission", "is")
            if batch
            else ("Try it and Guardrail test", "are")
        )
        message = (
            f"Unavailable in this release: {surface} {verb} turned off on this "
            "deployment."
        )
    return Response(
        build_error_envelope(
            message,
            status_code=409,
            code="feature_unavailable_733",
            extra={"known_issue": "KI-733-BATCH" if batch else "KI-733-PG"},
        ),
        status=409,
    )
