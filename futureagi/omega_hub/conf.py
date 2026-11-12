"""Hub configuration (SPEC §11): each value from Django settings, else the environment, else a default.

Read lazily on every call so tests can override with `settings` fixtures and environments can
change without a settings.py edit.
"""

from __future__ import annotations

import os

from django.conf import settings

DISCORD_URL = "https://discord.com/invite/n2tCUKBkAw"
# The maintainer organisation omega_hub_seed_dev creates. omega_hub_dev_approve approves only for
# this one, so a local shell pointed at a real database (a port-forward) cannot approve anything there.
DEV_MAINTAINER_ORG_NAME = "Future AGI (Omega Hub maintainers, dev)"


def _get(name: str, default: str | None = None) -> str | None:
    value = getattr(settings, name, None)
    if value is None:
        value = os.environ.get(name, default)
    return value if value not in ("",) else default


def maintainer_org_id() -> str | None:
    return _get("OMEGA_HUB_MAINTAINER_ORG_ID")


def storage_kind() -> str:
    return (_get("OMEGA_HUB_STORAGE", "object") or "object").lower()


def storage_dir() -> str | None:
    return _get("OMEGA_HUB_STORAGE_DIR")


def bucket() -> str:
    return _get("OMEGA_HUB_BUCKET", "omega-hub") or "omega-hub"


def checker_token() -> str | None:
    # Standalone and Distributed supply this marker, including the empty string.
    # A generated secret must not enable readiness, intake or authentication when
    # Compose will not start the checker. Production pins hub for its always-on
    # external-runner checker; Helm omits the marker.
    profiles = getattr(settings, "OMEGA_HUB_COMPOSE_PROFILES", None)
    if profiles is None:
        profiles = os.environ.get("OMEGA_HUB_COMPOSE_PROFILES")
    if profiles is not None:
        enabled = {profile.strip() for profile in str(profiles).split(",")}
        if not {"hub", "*"}.intersection(enabled):
            return None
    token = _get("OMEGA_HUB_CHECKER_TOKEN")
    if (
        not token
        or not token.strip()
        or token == "local-dev-only-hub-checker-token-replace-me"
    ):
        return None
    return token


def metrics_token() -> str | None:
    """Dedicated read-only scrape credential; never reuse the checker secret."""
    return _get("OMEGA_HUB_METRICS_TOKEN")


def app_url() -> str:
    return (
        _get("OMEGA_HUB_APP_URL", "http://localhost:3031/dashboard/hub") or ""
    ).rstrip("/")


def public_api_url() -> str:
    return (
        _get("OMEGA_HUB_PUBLIC_API_URL", "http://127.0.0.1:8000/omega-hub/v1") or ""
    ).rstrip("/")


def client_ip_header() -> str | None:
    """A header the edge proxy always overwrites with the client address (client_ip.py, HUB-04)."""
    return _get("OMEGA_HUB_CLIENT_IP_HEADER")


def trusted_proxy_depth_configured() -> bool:
    return _get("OMEGA_HUB_TRUSTED_PROXY_DEPTH") is not None or bool(client_ip_header())


def trusted_proxy_depth() -> int:
    """Proxies in front of Django that append to X-Forwarded-For; 0 trusts REMOTE_ADDR only."""
    try:
        return max(0, int(str(_get("OMEGA_HUB_TRUSTED_PROXY_DEPTH", "0"))))
    except ValueError:
        return 0


def counted_downloads_per_address() -> int:
    """Downloads one address may add to the counts per day, across every version (HUB-04; provisional)."""
    try:
        return max(
            0, int(str(_get("OMEGA_HUB_COUNTED_DOWNLOADS_PER_ADDRESS_DAY", "30")))
        )
    except ValueError:
        return 30


def allow_dev_approve() -> bool:
    return str(_get("OMEGA_HUB_ALLOW_DEV_APPROVE", "0")).lower() in ("1", "true", "yes")


LOCAL_ENV_TYPES = ("local", "test")


def is_local_env() -> bool:
    """The platform's own local marker: `ENV_TYPE` local or test (tfc/settings/settings.py).

    Staging and production deployments set `ENV_TYPE` (deploy/ uses `production`), so a stray
    `DEBUG=1` there does not count as local.
    """
    return str(getattr(settings, "ENV_TYPE", "") or "").lower() in LOCAL_ENV_TYPES


def debug_or_test() -> bool:
    """DEBUG, or the test settings (which set DEBUG=False and TESTING=True). Gates filesystem storage (SPEC §11)."""
    return bool(
        getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False)
    )


def is_local_dev() -> bool:
    """A local environment AND (DEBUG, or the test settings). Gates the dev seed and dev-approve shortcut.

    DEBUG alone is not enough: it is an environment switch that any deployment can turn on.
    """
    return debug_or_test() and is_local_env()


def ops_alert_emails() -> list[str]:
    """Addresses every Hub operations page also goes to (an on-call mailbox), comma-separated.

    Maintainers are paged too; this route still reaches someone when no maintainer is granted yet, or
    OMEGA_HUB_MAINTAINER_ORG_ID is missing (services/notify.ops_alert).
    """
    raw = _get("OMEGA_HUB_OPS_ALERT_EMAILS", "") or ""
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    return sorted({str(part).strip() for part in parts if str(part).strip()})


def support_timezone() -> str:
    """The IANA zone of the security-report SLA's business hours (services/support_hours.py)."""
    return _get("OMEGA_HUB_SUPPORT_TIMEZONE", "UTC") or "UTC"


def support_hours() -> str:
    """The support day, `HH:MM-HH:MM` in support_timezone(), Monday to Friday."""
    return _get("OMEGA_HUB_SUPPORT_HOURS", "09:00-17:00") or "09:00-17:00"


def support_holidays() -> str:
    """Comma-separated `YYYY-MM-DD` dates (in support_timezone()) on which the SLA clock does not run."""
    raw = _get("OMEGA_HUB_SUPPORT_HOLIDAYS", "") or ""
    return ",".join(raw) if isinstance(raw, (list, tuple)) else str(raw)
