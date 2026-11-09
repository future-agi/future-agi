"""Operator switch for background trace analysis, not interactive Falcon.

Follow omega_rollout.readiness's settings-first, environment-fallback pattern.
Read on each dispatch; only an explicit off value disables the existing behavior.
This is independent of Falcon/Error Feed license and entitlement decisions.
"""

import os

from django.conf import settings

SETTING = "FALCON_BACKGROUND_ANALYSIS_ENABLED"
BACKGROUND_ANALYSIS_UNAVAILABLE_MESSAGE = (
    "Deep analysis isn't available right now, so no analysis was started."
)


def background_analysis_enabled() -> bool:
    value = getattr(settings, SETTING, None)
    if value is None:
        value = os.environ.get(SETTING, "")
    if value is False:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    # Missing, blank, malformed and non-boolean settings leave the default ON.
    return True
