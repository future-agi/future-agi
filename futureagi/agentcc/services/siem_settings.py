"""The deployment switch for SIEM export. Off unless the operator turns it on.

``AGENTCC_SIEM_EXPORT_ENABLED`` (a Django setting, else the environment
variable of that name) must be a true value for any destination to be enabled
or any export to be queued or sent. Destinations can be added and inspected
while it is off; they stay disabled.
"""

import os

from django.conf import settings

SETTING = "AGENTCC_SIEM_EXPORT_ENABLED"
_TRUE = {"1", "true", "t", "yes", "on"}


def export_enabled():
    value = getattr(settings, SETTING, None)
    if value is None:
        value = os.environ.get(SETTING, "")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE
