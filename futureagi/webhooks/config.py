"""Install-level settings for the webhooks adapters (DESIGN.md sections 6.1, 6.2, 7.3 and 13.1).

Every value is read here and handed to the core as plain arguments; the core never reads settings. Environment
names follow the design: ``FI_WEBHOOKS_*`` are operator settings (ignored on hosted where the design says so),
``WEBHOOKS_*`` are deployment settings.
"""

from __future__ import annotations

import functools
import logging
import os

from webhooks.core.ipclass import Profile

log = logging.getLogger("webhooks.config")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _flag(name: str) -> bool:
    return _env(name).lower() in ("1", "t", "true", "yes", "on")


def _list(name: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in _env(name).split(",") if x.strip())


def is_hosted() -> bool:
    """``DeploymentMode.is_cloud()`` where the EE module is present, else the CLOUD_DEPLOYMENT region check."""
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        from tfc.ee_loader import is_cloud_env

        return is_cloud_env()
    return DeploymentMode.is_cloud()


def debug_enabled() -> bool:
    from django.conf import settings

    return bool(getattr(settings, "DEBUG", False))


def time_scale(*, hosted: bool | None = None, debug: bool | None = None) -> float:
    """``FI_WEBHOOKS_TIME_SCALE`` (R10, WT-34): scales every delay, max age and health window, for the test plan
    only. Ignored (1.0) on hosted or with DEBUG off, and its presence there logs an error."""
    raw = _env("FI_WEBHOOKS_TIME_SCALE")
    if not raw:
        return 1.0
    hosted = is_hosted() if hosted is None else hosted
    debug = debug_enabled() if debug is None else debug
    if hosted or not debug:
        log.error(
            "FI_WEBHOOKS_TIME_SCALE is set but ignored: it applies only off hosted with DEBUG on",
            extra={
                "metric": "webhooks.config.time_scale_ignored",
                "hosted": hosted,
                "debug": debug,
            },
        )
        return 1.0
    try:
        value = float(raw)
    except ValueError:
        log.error("FI_WEBHOOKS_TIME_SCALE=%r is not a number; ignored", raw)
        return 1.0
    if not 0.0001 <= value <= 1.0:
        log.error("FI_WEBHOOKS_TIME_SCALE=%r is outside 0.0001-1; ignored", raw)
        return 1.0
    return value


def profile(*, hosted: bool | None = None) -> Profile:
    """The address-rule profile. On hosted the operator escapes (http, private CIDRs, private hosts) are ignored."""
    hosted = is_hosted() if hosted is None else hosted
    deny = tuple(h.lower() for h in _list("WEBHOOKS_DENY_HOSTS"))
    if hosted:
        return Profile(mode="hosted", deny_hosts=deny)
    ports = tuple(int(p) for p in _list("FI_WEBHOOKS_PRIVATE_PORTS")) or (443,)
    return Profile(
        mode="self_hosted",
        allow_http=_flag("FI_WEBHOOKS_ALLOW_HTTP"),
        private_cidrs=_list("FI_WEBHOOKS_PRIVATE_CIDRS"),
        private_ports=ports,
        allowed_private_hosts=tuple(
            h.lower() for h in _list("FI_WEBHOOKS_ALLOWED_PRIVATE_HOSTS")
        ),
        deny_hosts=deny,
    )


def extra_ca_file(*, hosted: bool | None = None) -> str | None:
    """``FI_WEBHOOKS_EXTRA_CA_FILE`` (a private CA) on self-hosted and dev; never on hosted."""
    hosted = is_hosted() if hosted is None else hosted
    path = _env("FI_WEBHOOKS_EXTRA_CA_FILE")
    return None if hosted or not path else path


def sending_enabled() -> bool:
    """``FI_WEBHOOKS_ENABLED=false`` turns the dispatcher off; events still queue."""
    return _env("FI_WEBHOOKS_ENABLED", "true").lower() not in (
        "0",
        "f",
        "false",
        "no",
        "off",
    )


HOSTED_RESOLVER_BUILT = False
"""Hosted resolution through ``WEBHOOKS_RESOLVERS`` (public recursive resolvers behind the webhook NAT, never cluster
DNS; section 6.2) needs a DNS client and lands with WH-1c. Until then a hosted install refuses to send."""


def hosted_send_blockers(*, hosted: bool | None = None) -> list[str]:
    """Why a hosted install must not send yet; empty off hosted. The hosted gates of section 6.2 and 6.4 live here in
    code, not only in a runbook: the public resolvers, the operator deny list, and the resolver that uses them."""
    hosted = is_hosted() if hosted is None else hosted
    if not hosted:
        return []
    out = []
    if not _list("WEBHOOKS_RESOLVERS"):
        out.append(
            "WEBHOOKS_RESOLVERS is not set (hosted never resolves through cluster DNS)"
        )
    if not HOSTED_RESOLVER_BUILT:
        out.append("resolution through WEBHOOKS_RESOLVERS is not built yet (WH-1c)")
    if not _list("WEBHOOKS_DENY_HOSTS"):
        out.append(
            "WEBHOOKS_DENY_HOSTS is not set (the platform's own and internal hosts)"
        )
    return out


def send_blockers(*, hosted: bool | None = None) -> list[str]:
    """Every reason the dispatcher must idle instead of sending on this install; empty when it may send."""
    out = [] if sending_enabled() else ["FI_WEBHOOKS_ENABLED=false"]
    return out + hosted_send_blockers(hosted=hosted)


def dispatcher_loops(*, hosted: bool | None = None) -> int:
    hosted = is_hosted() if hosted is None else hosted
    raw = _env("WEBHOOKS_DISPATCHER_LOOPS")
    return max(1, int(raw)) if raw.isdigit() else (2 if hosted else 1)


def listen_dsn() -> str | None:
    """A direct (non-pgbouncer) DSN for ``LISTEN webhooks``; without one the 1 s poll alone runs."""
    return _env("WEBHOOKS_LISTEN_DSN") or None


@functools.lru_cache(maxsize=1)
def _extractor():
    import tldextract

    # The bundled snapshot only: no fetch of the list, no cache directory, ICANN section only.
    return tldextract.TLDExtract(
        suffix_list_urls=(), cache_dir=None, include_psl_private_domains=False
    )


def public_suffix(host: str) -> str | None:
    """The ICANN-section public suffix of ``host``, or None when the top-level label is not a delegated TLD."""
    suffix = _extractor()(host).suffix
    return suffix or None
