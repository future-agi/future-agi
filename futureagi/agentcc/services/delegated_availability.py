"""Availability answers shared by the console's delegated operations."""

import os

from django.conf import settings
from rest_framework.response import Response
from tfc.utils.api_errors import build_error_envelope


def delegated_enabled(*, batch=False):
    name = (
        "AGENTCC_DELEGATED_BATCH_ENABLED"
        if batch
        else "AGENTCC_DELEGATED_PLAYGROUND_ENABLED"
    )
    # Operators enable only with the matching delegated gateway routes present.
    value = getattr(settings, name, os.environ.get(name, "false"))
    return value is True or value == "true"


def unavailable_response(*, batch=False):
    surface, verb = (
        ("Batch submission", "is") if batch else ("Try it and Guardrail test", "are")
    )
    return Response(
        build_error_envelope(
            f"Unavailable in this release: {surface} {verb} turned off on this deployment.",
            status_code=409,
            code="feature_unavailable_733",
            extra={"known_issue": "KI-733-BATCH" if batch else "KI-733-PG"},
        ),
        status=409,
    )
