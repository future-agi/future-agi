"""Independent F05 admission boundary; F50 split selection is not owned here.

OFF removes feature inputs before validated_request's strict unknown-field
check. No settings, snapshots or execution state are rewritten by this gate.
"""

from django.conf import settings

SCOPE_FIELDS = ("row_ids", "sample", "metadata", "error_policy", "limits")


def scope_enabled():
    return getattr(settings, "EXPERIMENT_SCOPE_ENABLED", False) is True


def without_scope_fields(fields):
    if not scope_enabled():
        for name in SCOPE_FIELDS:
            fields.pop(name, None)
    return fields
