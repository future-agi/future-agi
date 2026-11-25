"""Bound application event fields before the SDK's in-app walk and serializer."""

from typing import TYPE_CHECKING, cast

import sentry_sdk
from sentry_sdk.integrations import Integration

from tfc.logging.sentry import _expected_log_outcome, _scrub_event, _vertex_log_message

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint


class PayloadBoundsIntegration(Integration):
    identifier = "tfc_payload_bounds"

    @staticmethod
    def setup_once() -> None:
        sentry_sdk.get_global_scope().add_event_processor(_bound_payload)


def _bound_payload(event: "Event", hint: "Hint") -> "Event | None":
    if sentry_sdk.get_client().get_integration(PayloadBoundsIntegration) is None:
        return event

    # SDK 2.49 applies the scope before handle_in_app, EventScrubber and
    # serialize. This bounds fields already on the event, not traceback capture,
    # later request processors, or the SDK's subsequent attach_stacktrace.
    try:
        payload = cast(dict, event)
        if _expected_log_outcome(payload, hint):
            return None  # Log-only; classify before its structured message is rebuilt.
        record = hint.get("log_record")
        _scrub_event(
            payload,
            log_record=record,
            vertex_log=_vertex_log_message(
                getattr(record, "name", None), getattr(record, "msg", None)
            ),
        )
    except Exception:  # noqa: BLE001 - a processor exception would forward the original
        return None
    return event
