"""Baseline-id resolution for the drawer's compare-with-baseline view."""


def resolve_baseline_id(row_metadata, *, is_replay):
    """Pick the baseline trace/session id from a Row's metadata.

    Chat replays store the baseline under ``session_id``, voice replays
    under ``trace_id``. Generated replay-session scenarios fall back to
    ``intent_id``. Order matches the list and detail views; keep them
    consistent with this helper.
    """
    if not isinstance(row_metadata, dict):
        return None
    return (
        row_metadata.get("session_id")
        or row_metadata.get("trace_id")
        or (row_metadata.get("intent_id") if is_replay else None)
    )
