"""A call's ``ended_reason``: the canonical vocabulary, and where a reason came
from.

The hosted voice path (Agent Learning Kit) emits snake_case reason codes, while
the native path and everything downstream (evals, analytics, filters) use the
Vapi-style hyphenated vocabulary — e.g. ``silence-timed-out``, which the native
call-execution workflow branches on. Applying this single mapping at the
ingestion boundary keeps the platform storing only canonical values.

To drop the translation entirely, delete ``to_canonical_ended_reason`` and its
one call site in ``services/alk_simulate_ingestion``.

Every write of a call's end reason also records where the reason came from, in
``call_metadata[ENDED_REASON_SOURCE_KEY]``, through ``set_ended_reason``, with a
digest of the reason it was recorded for. Only our code at the place a reason
is written sets it; it is never copied from a request or a provider payload. A
public shared link to a call shows the stored reason only when the source
recorded for that same reason is one it may show, or the reason is one of our
fixed sentences (``ended_reason_for_shared_link``); a reason written without
``set_ended_reason`` shows ``SHARED_LINK_ENDED`` there. The organization's own
answers show the stored text.
"""

import hashlib

from django.db import transaction

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


def canonical_ended_reasons() -> list[str]:
    """Every canonical reason a hosted call can end with, sorted.

    The column is free-form, so this is the vocabulary a hosted run produces
    rather than an exhaustive set: a provider-native reason passes through
    untranslated and will not appear here.
    """
    return sorted(set(_SDK_TO_CANONICAL.values()))


def to_canonical_ended_reason(value):
    """Return the canonical ended_reason for an SDK code.

    ``None``/empty is returned as-is; unknown values pass through unchanged
    (``ended_reason`` is a free-form column, so nothing is ever dropped).
    """
    if not value:
        return value
    return _SDK_TO_CANONICAL.get(str(value).strip().lower(), value)


# The call_metadata key that holds where a call's end reason came from, as
# "<source>:<digest of the reason>". No call answer sends it.
ENDED_REASON_SOURCE_KEY = "ended_reason_source"

# call_metadata keys that hold a secret: a customer's provider or LiveKit
# credentials, kept for the workers that place a call, or a key a client sent.
# No call answer sends them, at any depth of call_metadata.
CALL_METADATA_SECRET_KEYS = frozenset(
    {
        "customer_livekit_api_key",
        "customer_livekit_api_secret",
        "customer_api_key",
        "client_api_key",
        "api_key",
        "api_secret",
        "user_api_key",
    }
)


class EndedReasonSource:
    """Where a call's stored end reason came from."""

    # A fixed sentence from our code.
    FIXED = "fixed"
    # The simulator's own end reason (its endCall tool's reason).
    SIMULATOR = "simulator"
    # A reason from the usage service or the usage check.
    USAGE = "usage"
    # A provider's enumerated end reason (Vapi endedReason, Retell
    # disconnection_reason, LiveKit's reason, the hosted harness stop_reason).
    PROVIDER = "provider"
    # A provider's free-text error message. Bland keeps its error message and
    # its end reason in one field, so every Bland end reason is this.
    PROVIDER_ERROR = "provider-error"
    # The organization's own text (a reason its client sends).
    ORGANIZATION = "organization"


_SOURCES = frozenset(
    {
        EndedReasonSource.FIXED,
        EndedReasonSource.SIMULATOR,
        EndedReasonSource.USAGE,
        EndedReasonSource.PROVIDER,
        EndedReasonSource.PROVIDER_ERROR,
        EndedReasonSource.ORGANIZATION,
    }
)
# The sources whose reason a public shared link shows as stored.
_SHARED_LINK_SOURCES = _SOURCES - {EndedReasonSource.PROVIDER_ERROR}

# What a public shared link shows for a reason it does not show as stored.
SHARED_LINK_ENDED = "The call ended."

# Every fixed sentence our code stores as a call's end reason, and the
# canonical vocabulary above: a shared link shows these as stored, with or
# without a source. Listed as literals: the modules that define them
# (services/test_executor, services/chat_sim, views/run_test) import this one,
# so importing them here would be a cycle, and rerun_coordinator_workflow is a
# Temporal workflow module whose import would load Temporal and the simulate
# models into every module that imports this one.
# simulate/tests/test_ended_reason_source.py checks each literal against its
# module's constant.
SHARED_LINK_FIXED_REASONS = frozenset(
    {
        # services/test_executor.py: CALL_STOPPED, CALL_COULD_NOT_START,
        # USAGE_CHECK_FAILED, the outbound call's missing key, and a cancelled
        # test.
        "The call stopped because of an error. Re-run it.",
        "The call could not start because of an error. Re-run it.",
        "The usage check could not be completed, so the call was ended.",
        "Configuration error: Missing API key for outbound call.",
        "Cancelled by user",
        # services/chat_sim.py: CHAT_COULD_NOT_START, CHAT_STOPPED and the chat
        # service's own refusals (SEND_REFUSAL_REASONS).
        "The chat could not start because of an error. Re-run it.",
        "The chat stopped because of an error. Re-run it.",
        "Session not found",
        "Chat has already ended",
        "Simulator returned an empty message twice",
        # services/futureagi_chat/service.py and services/vapi_chat/service.py.
        "Max conversation turns reached",
        "Chat ended by simulator",
        # views/run_test.py: the failed-call PATCH and RERUN_DISPATCH_FAILED.
        "Error processing simulation",
        "Rerun dispatch failed. Re-run it.",
        # temporal/workflows/rerun_coordinator_workflow.py: a cancelled rerun
        # and RERUN_STOPPED.
        "Rerun cancelled by user",
        "The rerun stopped because of an error. Re-run it.",
        # views/livekit_api.py: a finished room's reason when none was stored.
        "call_completed",
        *_SDK_TO_CANONICAL.values(),
    }
)


def _reason_digest(reason) -> str:
    """The first 16 hex digits of the reason's SHA-256; "" for no reason."""
    if not reason:
        return ""
    return hashlib.sha256(str(reason).encode("utf-8")).hexdigest()[:16]


def set_ended_reason(call, reason, source) -> list[str]:
    """Set ``call.ended_reason`` and record where it came from.

    ``source`` is an ``EndedReasonSource`` value chosen by the code that writes
    the reason. The record holds a digest of ``reason``, so it counts only
    while the call still holds that reason. Only the one key in
    ``call.call_metadata`` changes; every other key is kept. Returns the
    fields to save.
    """
    if source not in _SOURCES:
        raise ValueError(f"Unknown end reason source: {source!r}")
    call.ended_reason = reason
    if not isinstance(call.call_metadata, dict):
        call.call_metadata = {}
    call.call_metadata[ENDED_REASON_SOURCE_KEY] = f"{source}:{_reason_digest(reason)}"
    return ["ended_reason", "call_metadata"]


def end_calls_with_reason(calls, reason, source, **fields) -> int:
    """Set ``fields`` and the end reason, with its source, on every call in the
    queryset ``calls``, one locked row at a time. Returns how many it wrote.

    It stands in for a queryset ``update()``, which cannot set one key inside
    ``call_metadata``. The rows are locked when they are read, so ``calls``'s
    filter holds when they are written, as it did for the update.
    """
    written = 0
    with transaction.atomic():
        for call in calls.select_for_update():
            for name, value in fields.items():
                setattr(call, name, value)
            update_fields = set_ended_reason(call, reason, source)
            call.save(update_fields=[*fields, *update_fields])
            written += 1
    return written


def _recorded_source(metadata, reason):
    """The source ``metadata`` records for ``reason``: None when it records no
    known source, or records one for another reason."""
    marker = (
        metadata.get(ENDED_REASON_SOURCE_KEY) if isinstance(metadata, dict) else None
    )
    if not isinstance(marker, str):
        return None
    source, separator, digest = marker.partition(":")
    if not separator or source not in _SOURCES or digest != _reason_digest(reason):
        return None
    return source


def recorded_ended_reason_source(call):
    """The source recorded for the end reason ``call`` holds now, or None.

    A reason written without ``set_ended_reason`` (a queryset ``update``, a
    voice engine's own save, a reset row) has none.
    """
    return _recorded_source(call.call_metadata, call.ended_reason)


def stored_ended_reason_source(metadata, reason):
    """The source recorded for a call's stored ``reason`` in the call's own
    ``metadata``, for writing that same reason again. A stored reason with no
    source recorded for it is written again as ``PROVIDER_ERROR``, so a shared
    link keeps showing ``SHARED_LINK_ENDED`` for it."""
    return _recorded_source(metadata, reason) or EndedReasonSource.PROVIDER_ERROR


def _without_secret_keys(value):
    """``value`` without the keys in CALL_METADATA_SECRET_KEYS, at any depth."""
    if isinstance(value, dict):
        return {
            key: _without_secret_keys(item)
            for key, item in value.items()
            if key not in CALL_METADATA_SECRET_KEYS
        }
    if isinstance(value, list):
        return [_without_secret_keys(item) for item in value]
    return value


def without_ended_reason_source(metadata):
    """``metadata`` without an end-reason source, for merging a provider's
    metadata into a call's own: only our code sets the source."""
    if not isinstance(metadata, dict):
        return metadata
    return {
        key: value for key, value in metadata.items() if key != ENDED_REASON_SOURCE_KEY
    }


def call_metadata_for_answer(metadata):
    """A call's ``metadata`` as an answer sends it: without the end reason's
    source, and without a key named in CALL_METADATA_SECRET_KEYS at any depth.
    Every other key is sent as stored. The call list, annotation-queue items
    and their export send a call's metadata through this."""
    return _without_secret_keys(without_ended_reason_source(metadata))


def shared_link_ended_reason(reason, source=None):
    """The end reason a public shared link shows for a stored ``reason``.

    A fixed sentence shows as stored; any other reason shows only when its
    ``source`` is one a shared link may show. A provider's error text, and a
    reason with no source (a row no write site marked, a reason written without
    ``set_ended_reason``, or a rerun's snapshot), show ``SHARED_LINK_ENDED``.
    None and "" pass through.
    """
    if not reason:
        return reason
    if reason in SHARED_LINK_FIXED_REASONS:
        return reason
    if isinstance(source, str) and source in _SHARED_LINK_SOURCES:
        return reason
    return SHARED_LINK_ENDED


def ended_reason_for_shared_link(call):
    """The end reason a public shared link to ``call`` shows."""
    return shared_link_ended_reason(
        call.ended_reason, recorded_ended_reason_source(call)
    )
