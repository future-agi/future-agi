"""Whether a server accepts run settings; split selection is not owned here.

Off removes the run-setting fields from the create and detail serializers, and
the create serializer refuses a request that uses one with SCOPE_OFF_MESSAGE.
No settings, snapshots or execution state are rewritten by this gate.
"""

from django.conf import settings

SCOPE_FIELDS = ("row_ids", "sample", "metadata", "error_policy", "limits")

# What a person reads when a create uses a run setting this server turned off.
SCOPE_OFF_MESSAGE = (
    "Choosing rows, sampling, per-row limits, an error policy and experiment "
    "metadata are turned off on this server. Clear them to run, or ask your "
    "administrator."
)
SCOPE_OFF_FIELD_MESSAGE = "Turned off on this server."

# Per-row limits this release refuses, because the token, message and cost
# budget is not applied to the model call yet. The time limit (timeout_s) is
# accepted. Emptying this accepts them again; change the limits help text
# (serializers/experiment_run_scope.py, serializers/experiments.py) with it.
UNAVAILABLE_ROW_LIMITS = ("tokens", "messages", "cost_usd")
UNAVAILABLE_ROW_LIMITS_MESSAGE = (
    "Token, message and cost limits aren't available in this release. "
    "Clear them to run."
)


def scope_enabled():
    return getattr(settings, "EXPERIMENT_SCOPE_ENABLED", False) is True


def without_scope_fields(fields):
    if not scope_enabled():
        for name in SCOPE_FIELDS:
            fields.pop(name, None)
    return fields
