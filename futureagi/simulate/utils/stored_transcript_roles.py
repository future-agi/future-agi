"""Helpers for generic CallTranscript role filters."""

from simulate.models.test_execution import CallTranscript


def get_conversational_transcript_roles() -> list[str]:
    """Return roles that represent back-and-forth dialogue turns."""
    return [
        CallTranscript.SpeakerRole.USER,
        CallTranscript.SpeakerRole.ASSISTANT,
    ]


def get_displayable_transcript_roles() -> list[str]:
    """Return transcript roles that should be shown in transcript views."""
    return [
        CallTranscript.SpeakerRole.USER,
        CallTranscript.SpeakerRole.ASSISTANT,
        CallTranscript.SpeakerRole.SYSTEM,
    ]
