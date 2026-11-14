"""Request Logs retention settings for the gateway."""

import os

from django.core.exceptions import ImproperlyConfigured

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("", "0", "false", "no", "off")


def _boolean(name: str) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ImproperlyConfigured(f"{name} must be true or false.")


# The gateway seeds the budgets of its own YAML config from the request logs
# of the current month (its default budget period), and Django cannot read
# that YAML. So retention keeps the current UTC month unless the operator
# states here that the gateway YAML defines no budgets.
AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS = _boolean("AGENTCC_GATEWAY_YAML_HAS_NO_BUDGETS")
