"""The ``webhooks-health`` and ``webhooks-retention`` activities (DESIGN.md section 13.1). Sync activities on the
``webhooks`` queue; each opens and closes its own database connections."""

from __future__ import annotations

from datetime import UTC, datetime

from django.db import close_old_connections
from temporalio import activity

from webhooks import config


@activity.defn(name="webhooks_health_pass")
def health_pass() -> dict:
    from webhooks import health_pass as hp

    close_old_connections()
    try:
        return hp.run(datetime.now(UTC), scale=config.time_scale())
    finally:
        close_old_connections()


@activity.defn(name="webhooks_retention_pass")
def retention_pass() -> dict:
    from webhooks import retention

    close_old_connections()
    try:
        out = retention.run(datetime.now(UTC), scale=config.time_scale())
        activity.heartbeat()
        return out
    finally:
        close_old_connections()
