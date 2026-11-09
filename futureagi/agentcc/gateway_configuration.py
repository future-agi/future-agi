"""Validate the user-facing gateway address before hosted processes start."""

import ipaddress
import os
import re
from urllib.parse import urlsplit

import structlog
from django.core.exceptions import ImproperlyConfigured

from agentcc.hosted import deployment_profile
from tfc.ee_loader import is_cloud_env

logger = structlog.get_logger(__name__)

_PUBLIC_URL_ERROR = (
    "AGENTCC_GATEWAY_PUBLIC_URL must be set to an HTTPS URL with a non-local "
    "hostname on hosted deployments."
)
MISSING_PUBLIC_URL_MESSAGE = (
    "AGENTCC_GATEWAY_PUBLIC_URL is not configured on this hosted deployment; "
    "gateway URL not configured and readiness (/readyz/) fails."
)


def _is_hosted_gateway():
    """Support c2.5's explicit profile and the cloud deployment signal."""
    return deployment_profile() == "hosted" or is_cloud_env()


def hosted_gateway_public_url_missing():
    """The same configuration condition feeds startup, readiness and the UI."""
    return (
        _is_hosted_gateway()
        and not os.environ.get("AGENTCC_GATEWAY_PUBLIC_URL", "").strip()
    )


def _is_nonlocal_https_url(value):
    try:
        url = urlsplit(value)
        hostname = (url.hostname or "").lower().rstrip(".")
        # Accessing port also rejects malformed authorities without connecting.
        _ = url.port
    except ValueError:
        return False
    if url.scheme != "https" or not hostname:
        return False
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Browsers accept abbreviated, integer, octal and hex IPv4 hosts that
        # ipaddress rejects (127.1, 2130706433, 0177.0.0.1, 0x7f.1, 0).
        # Reject noncanonical numeric hosts rather than resolving them via DNS.
        # A bare hex prefix (0x) denotes zero in browser IPv4 parsing too.
        # Ordinary DNS names, including names with numeric labels, stay valid.
        return (
            re.fullmatch(
                r"(?:[0-9]+|0x[0-9a-f]*)(?:\.(?:[0-9]+|0x[0-9a-f]*))*", hostname
            )
            is None
        )
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (address.is_loopback or address.is_unspecified)


def validate_hosted_gateway_public_url():
    """Missing hosted config fails readiness; explicit invalid config fails boot."""
    if not _is_hosted_gateway():
        return
    if hosted_gateway_public_url_missing():
        # Only the AppConfig.ready hook logs this; readiness requests stay quiet.
        logger.error(MISSING_PUBLIC_URL_MESSAGE)
        return
    if not _is_nonlocal_https_url(os.environ.get("AGENTCC_GATEWAY_PUBLIC_URL", "")):
        # Never echo the supplied URL: startup logs only need the variable name.
        raise ImproperlyConfigured(_PUBLIC_URL_ERROR)
