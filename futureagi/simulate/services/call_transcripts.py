"""Storing a call's transcript: its rows, and beside each actor's turn who spoke it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from simulate.models.test_execution import CallTranscript, CallTranscriptActor


@dataclass(frozen=True)
class SpokenTurn:
    """One transcript row to store and, on an actor's turn, who spoke it."""

    row: CallTranscript
    speaker_name: str = ""
    actor_id: str | None = None


def store_transcript(turns: Sequence[SpokenTurn]) -> list[CallTranscript]:
    """Insert the rows, then who spoke each actor turn that names its speaker, in one bulk insert each. The caller
    clears the call's earlier transcript first; deleting a row deletes its speaker with it."""
    rows = CallTranscript.objects.bulk_create([turn.row for turn in turns])
    CallTranscriptActor.objects.bulk_create(
        [
            CallTranscriptActor(
                transcript=row,
                speaker_name=turn.speaker_name,
                actor_id=turn.actor_id,
            )
            for row, turn in zip(rows, turns, strict=True)
            if row.speaker_role == CallTranscript.SpeakerRole.ACTOR
            and (turn.speaker_name or turn.actor_id)
        ]
    )
    return rows
