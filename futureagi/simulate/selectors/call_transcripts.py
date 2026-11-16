"""Reading a call's transcript with who spoke each turn."""

from __future__ import annotations

from django.db.models import F, QuerySet, Value
from django.db.models.functions import Coalesce

from simulate.models.test_execution import CallTranscript


def with_speakers(transcripts: QuerySet[CallTranscript]) -> QuerySet[CallTranscript]:
    """The rows with who spoke each, read in the same query: ``speaker_name`` ("" unless an actor spoke it, or an
    older row) and ``actor_id`` (None likewise). Every reader of either reads through this."""
    return transcripts.annotate(
        speaker_name=Coalesce("actor_turn__speaker_name", Value("")),
        actor_id=F("actor_turn__actor_id"),
    )
