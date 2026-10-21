"""End-reason categories keep termination distinct from caller abandonment."""

import re

import pytest

from simulate.services.run_dashboard_v3 import END_REASONS


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("simulator_end_call", "Simulator ended"),
        ("simulator-ended-call", "Simulator ended"),
        ("target_end_call", "Agent ended"),
        ("assistant-ended-call", "Agent ended"),
        ("target_disconnected", "Agent disconnected"),
        ("target-disconnected", "Agent disconnected"),
        ("session_closed", "Session closed"),
        ("session-closed", "Session closed"),
        ("close_on_disconnect", "Session closed"),
        ("participant_disconnected", "Disconnected"),
        ("participant-disconnected", "Disconnected"),
        ("room_disconnected", "Disconnected"),
        ("room-disconnected", "Disconnected"),
        ("provider_disconnected", "Disconnected"),
        ("provider-disconnected", "Disconnected"),
        ("closing_loop", "Completed"),
        ("closing-loop", "Completed"),
        ("customer-ended-call", "Caller hung up"),
        ("conversation_silence_timeout", "Silence timeout"),
        ("conversation_timeout", "Time or turn limit"),
        ("some-new-provider-reason", "Unrecognised"),
    ],
)
def test_end_reason_categories(reason, expected):
    category = next(
        (label for label, pattern in END_REASONS if re.search(pattern, reason, re.I)),
        "Unrecognised",
    )
    assert category == expected
