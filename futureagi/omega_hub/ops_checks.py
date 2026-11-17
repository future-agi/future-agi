"""System checks for the Hub's operational wiring (`manage.py check`; `migrate` runs them too, so every
deploy shape's bootstrap prints them). They name what a deploy shape must set for the Hub to work:

- W004: OMEGA_HUB_CHECKER_TOKEN is unset. No checker can claim work, so uploads are refused with
  OMEGA_HUB_CHECKER_NOT_CONFIGURED (services/intake.require_checker) and the operations check pages
  omega_hub_checker_not_configured.
- W005: OMEGA_HUB_MAINTAINER_ORG_ID is unset. Nobody is a maintainer (permissions.is_maintainer), so
  nothing can be reviewed and maintainer pages have no maintainer to reach.
- W006 (database): no operations page can reach anyone: no active maintainer and no
  OMEGA_HUB_OPS_ALERT_EMAILS. The checker-stall and security-SLA pages end as
  `omega_hub_ops_alert_unrouted` log lines.
- W007: OMEGA_HUB_METRICS_TOKEN is unset, so /omega-hub/internal/ops/metrics answers 503 and the
  Hub's gauges cannot be scraped.
- W008: the support calendar (OMEGA_HUB_SUPPORT_*) does not parse; the SLA clock runs on 09:00-17:00
  UTC instead (services/support_hours.py).

W004, W005 and W007 apply outside a local environment only (conf.is_local_env), as W003 does. They
are warnings, not errors: an error would stop `migrate` in a shape that has not deployed the Hub's
checker yet, and with it every other product. omega_hub/ops/RUNBOOK.md, "What every deploy shape
sets", lists the values.
"""

from __future__ import annotations

from django.core.checks import Tags, Warning, register
from django.db import DatabaseError

from omega_hub import conf
from omega_hub.services import support_hours


@register()
def operations_unconfigured(app_configs=None, **kwargs):
    out = []
    if not conf.is_local_env():
        if not conf.checker_token():
            out.append(Warning(
                "OMEGA_HUB_CHECKER_TOKEN is not set: no Hub checker can claim work, so uploads are refused.",
                hint="Run the sandboxed checker (services/omega-hub-checker) and give it and the app the same "
                     "OMEGA_HUB_CHECKER_TOKEN.",
                id="omega_hub.W004",
            ))
        if not conf.maintainer_org_id():
            out.append(Warning(
                "OMEGA_HUB_MAINTAINER_ORG_ID is not set: nobody is a Hub maintainer, so nothing can be reviewed.",
                hint="Set it to the maintainer organisation's id and grant each maintainer with "
                     "`manage.py omega_hub_maintainer grant`.",
                id="omega_hub.W005",
            ))
        if not conf.metrics_token():
            out.append(Warning(
                "OMEGA_HUB_METRICS_TOKEN is not set: the Hub's operations metrics cannot be scraped.",
                hint="Set a scrape-only secret (never the checker token) and send it as X-Omega-Hub-Metrics-Token.",
                id="omega_hub.W007",
            ))
    out.extend(Warning(problem, hint="Fix the setting; until then the SLA clock uses 09:00-17:00 UTC.",
                       id="omega_hub.W008") for problem in support_hours.problems())
    return out


@register(Tags.database)
def operations_pages_unrouted(app_configs=None, databases=None, **kwargs):
    if conf.is_local_env() or "default" not in (databases or ()):
        return []
    from omega_hub.services import notify

    try:
        recipients = notify.ops_recipients()
    except DatabaseError:
        return []  # before migrate: the tables are not there yet
    if recipients:
        return []
    return [Warning(
        "No Hub operations page can reach anyone: there is no active maintainer and no OMEGA_HUB_OPS_ALERT_EMAILS.",
        hint="Grant the maintainers (`manage.py omega_hub_maintainer grant`) or set OMEGA_HUB_OPS_ALERT_EMAILS "
             "to an on-call mailbox.",
        id="omega_hub.W006",
    )]
