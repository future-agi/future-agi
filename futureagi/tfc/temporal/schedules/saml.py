"""Temporal schedule for reclaiming expired SAML login state."""

from tfc.temporal.schedules.config import ScheduleConfig

SAML_SCHEDULES = [
    ScheduleConfig(
        schedule_id="saml-purge-login-state",
        activity_name="purge_login_state",
        interval_seconds=60,
        queue="default",
        description="Purge expired SAML candidates and retained attempts",
    )
]
