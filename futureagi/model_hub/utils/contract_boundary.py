"""Runtime contract validation at view boundaries.

`responses={200: FooSerializer}` on a view only documents the schema for the
generated client — drf-yasg never runs the actual response dict through the
serializer. So when a hand-built response drifts from its serializer (renamed
key, wrong type on a real row), nothing fails: not at runtime, not in CI, not
in the contract test if the test only seeds an empty response.

`validate_response_contract` closes that gap:

- In DEBUG or TESTING: raise AssertionError so CI fails on drift.
- In prod: log the drift as an error and return unchanged so users still see
  their data (fail-open — a contract bug should not page oncall).

Use it once, right before the `success_response(...)` return, against the
result serializer (the inner one, not the {status, result} wrapper).
"""
import logging

import structlog
from django.conf import settings

logger = structlog.get_logger(__name__)


def validate_response_contract(serializer_cls, response_data, *, view_name):
    """Validate response_data against serializer_cls at the view boundary.

    Args:
        serializer_cls: The *result* serializer class (e.g. EvalUsageStatsResponseResultSerializer)
            — NOT the {status, result} wrapper.
        response_data: The dict the view is about to return.
        view_name: Identifier for logs (the view's class name).

    Returns:
        response_data unchanged. The return is convenience for callers that
        prefer `return success_response(validate_response_contract(...))`.

    Raises:
        AssertionError: In DEBUG or TESTING when the response drifts from the
            contract. In prod the drift is logged, not raised.
    """
    is_strict = bool(getattr(settings, "DEBUG", False)) or bool(
        getattr(settings, "TESTING", False)
    )
    try:
        # instance= mode runs to_representation on each declared field.
        # Triggering `.data` raises on missing required fields and on
        # to_representation errors (wrong type, unserializable value).
        _ = serializer_cls(instance=response_data).data
    except Exception as exc:
        msg = f"Response contract drift in {view_name}: {exc}"
        if is_strict:
            raise AssertionError(msg) from exc
        logger.error(
            "response_contract_drift",
            view=view_name,
            error=str(exc),
            exc_info=True,
        )
    return response_data
