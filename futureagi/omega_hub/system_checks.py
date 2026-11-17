"""System checks for the Hub (`manage.py check`; `migrate` runs the database ones too).

- W001: `OMEGA_HUB_ALLOW_DEV_APPROVE` is on outside a local environment. The command refuses to run
  there anyway (conf.is_local_dev); the warning makes the stray setting visible.
- W002 (database): versions stored before strict SemVer (migration 0002 adds its check constraint
  NOT VALID, so they stay readable). Each needs a maintainer's yank or removal; none is renamed,
  because a version string is never reused or changed (SPEC §2.3).
- W003: outside a local environment, neither `OMEGA_HUB_TRUSTED_PROXY_DEPTH` nor
  `OMEGA_HUB_CLIENT_IP_HEADER` is set (HUB-04). The Hub then trusts only `REMOTE_ADDR`, which is safe
  but, behind a load balancer, makes every anonymous client one address: the 120/min anonymous
  throttle becomes global. Set the depth to the real number of proxies (0 when Django faces clients).
"""

from __future__ import annotations

from django.core.checks import Tags, Warning, register
from django.db import DatabaseError
from django.db.models import Count

from omega_hub import conf, semver

LEGACY_LIMIT = 20


@register()
def dev_approve_outside_local(app_configs=None, **kwargs):
    if conf.allow_dev_approve() and not conf.is_local_dev():
        return [Warning(
            "OMEGA_HUB_ALLOW_DEV_APPROVE is set outside a local environment; omega_hub_dev_approve refuses to run here.",
            hint="Unset it. The dev shortcut needs ENV_TYPE local or test, and DEBUG or the test settings.",
            id="omega_hub.W001",
        )]
    return []


@register()
def client_address_unconfigured(app_configs=None, **kwargs):
    if conf.is_local_env() or conf.trusted_proxy_depth_configured():
        return []
    return [Warning(
        "The Hub does not know how many proxies sit in front of Django; it trusts REMOTE_ADDR only.",
        hint="Set OMEGA_HUB_TRUSTED_PROXY_DEPTH to the number of proxies that append to X-Forwarded-For "
             "(0 if none), or OMEGA_HUB_CLIENT_IP_HEADER to a header the edge proxy overwrites.",
        id="omega_hub.W003",
    )]


def legacy_version_rows(using: str = "default") -> tuple[list[str], list[str]]:
    """(versions that are not strict SemVer, entries with two versions on one sort key), as refs."""
    from omega_hub.models import Version

    rows = Version.objects.using(using).select_related("entry__publisher")
    loose = [f"{row.entry.publisher.handle}/{row.entry.name}@{row.version}"
             for row in rows.exclude(version__regex=semver.SQL_REGEX).order_by("entry_id", "version")[:LEGACY_LIMIT]]
    clashes = (Version.objects.using(using).values("entry__publisher__handle", "entry__name", "semver_key")
               .annotate(n=Count("id")).filter(n__gt=1).order_by("entry__publisher__handle", "entry__name"))
    shared = [f"{row['entry__publisher__handle']}/{row['entry__name']} ({row['n']} versions share {row['semver_key']})"
              for row in clashes[:LEGACY_LIMIT]]
    return loose, shared


@register(Tags.database)
def legacy_versions(app_configs=None, databases=None, **kwargs):
    out = []
    for alias in databases or ():
        try:
            loose, shared = legacy_version_rows(alias)
        except DatabaseError:
            continue  # before migrate: the table is not there yet
        if loose:
            out.append(Warning(
                f"Hub versions stored before strict SemVer: {', '.join(loose)}",
                hint="Yank or remove each one as a maintainer; a new upload must use a version with no leading zeros.",
                id="omega_hub.W002",
            ))
        if shared:
            out.append(Warning(
                f"Hub entries with two versions on one sort key: {', '.join(shared)}",
                hint="`latest` takes the most recently approved of them; yank the other.",
                id="omega_hub.W002",
            ))
    return out
