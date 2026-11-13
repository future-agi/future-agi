"""Helpers for generic CallTranscript role filters."""

from simulate.models.test_execution import CallTranscript


def call_actor_label(name: str | None) -> str:
    """How a transcript a model reads names an actor, in the harness's words, so no one reads them as the caller."""
    return f"{name or 'Someone else'} (not the person being served)"


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
        CallTranscript.SpeakerRole.ACTOR,
        CallTranscript.SpeakerRole.TASK,
    ]
