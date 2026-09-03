"""Translate hosted (SDK) ``ended_reason`` codes to the platform's canonical
vocabulary.

The hosted voice path (Agent Learning Kit) emits snake_case reason codes, while
the native path and everything downstream (evals, analytics, filters) use the
Vapi-style hyphenated vocabulary — e.g. ``silence-timed-out``, which the native
call-execution workflow branches on. Applying this single mapping at the
ingestion boundary keeps the platform storing only canonical values.

To drop the translation entirely, delete this module and its one call site in
``services/alk_simulate_ingestion``.
"""

# SDK reason code -> canonical (Vapi-style) ended_reason. Unknown codes pass
# through unchanged, so provider-native reasons and future codes are preserved.
_SDK_TO_CANONICAL = {
    "simulator_end_call": "customer-ended-call",
    "customer_end_call": "customer-ended-call",
    "assistant_end_call": "assistant-ended-call",
    "target_end_call": "assistant-ended-call",
    "completed": "assistant-ended-call",
    "session_closed": "assistant-ended-call",
    "insufficient_conversation": "assistant-ended-call",
    "conversation_silence_timeout": "silence-timed-out",
    "conversation_settled": "silence-timed-out",
    "conversation_timeout": "max-duration-reached",
    "agent_unavailable": "no-answer",
    "participant_disconnected": "customer-ended-call",
    "close_on_disconnect": "customer-ended-call",
    "target_disconnected": "customer-ended-call",
    "cancelled": "cancelled",
}


def to_canonical_ended_reason(value):
    """Return the canonical ended_reason for an SDK code.

    ``None``/empty is returned as-is; unknown values pass through unchanged
    (``ended_reason`` is a free-form column, so nothing is ever dropped).
    """
    if not value:
        return value
    return _SDK_TO_CANONICAL.get(str(value).strip().lower(), value)
