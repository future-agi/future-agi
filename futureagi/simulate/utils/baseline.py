"""Baseline-id resolution for the drawer's compare-with-baseline view."""

import uuid


def _as_uuid(value):
    """Return ``value`` only when it is a real UUID string, else None.

    Every baseline is addressed by a session or trace UUID. Generated scenarios
    that were NOT seeded from transcripts carry synthetic ``UC-XX`` intent ids,
    and the ``intent_id`` fallback below would otherwise surface one of those as
    a baseline. Downstream that matches no traces and renders an empty
    comparison as a successful one, so reject it here — a hidden button beats a
    blank baseline.
    """
    if not isinstance(value, str):
        return None
    try:
        uuid.UUID(value)
    except ValueError:
        return None
    return value


def resolve_baseline_id(row_metadata, *, is_replay):
    """Pick the baseline session/trace id from a Row's metadata.

    ``intent_id`` is the only key written today: ``dataset_persister`` persists
    it, and for a transcript-seeded scenario that value IS the session or trace
    id, because intent extraction keys its dict off the transcript map.

    ``session_id`` is legacy — a pre-monorepo writer stored it until March 2026,
    when scenario persistence switched to ``intent_id``. ``trace_id`` has never
    been written. Both are kept ahead of the fallback so an explicitly persisted
    id outranks the inferred one.

    Every candidate is UUID-checked, so a ``UC-XX`` intent id never escapes.
    """
    if not isinstance(row_metadata, dict):
        return None

    return (
        _as_uuid(row_metadata.get("session_id"))
        or _as_uuid(row_metadata.get("trace_id"))
        or (_as_uuid(row_metadata.get("intent_id")) if is_replay else None)
    )
