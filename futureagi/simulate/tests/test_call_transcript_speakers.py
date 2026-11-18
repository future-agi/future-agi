"""Who spoke each transcript turn is written by the transcript service and read in the same query as the turns, so a
multi-turn read costs one query whatever the number of actor turns."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from simulate.models import CallTranscriptActor
from simulate.models.test_execution import CallTranscript
from simulate.selectors.call_transcripts import with_speakers
from simulate.serializers import CallTranscriptSerializer
from simulate.services.call_transcripts import SpokenTurn, store_transcript
from simulate.tests.test_rerun_api import (  # fixtures this file builds on
    agent_definition,
    call_execution,
    dataset_for_scenario,
    run_test,
    scenario,
    simulator_agent,
    test_execution,
)

pytestmark = pytest.mark.django_db

FIXTURES = (
    agent_definition,
    call_execution,
    dataset_for_scenario,
    run_test,
    scenario,
    simulator_agent,
    test_execution,
)
ACTOR = CallTranscript.SpeakerRole.ACTOR


def _turns(call, actor_turns):
    turns = [
        SpokenTurn(
            row=CallTranscript(call_execution=call, speaker_role="user", content="hi")
        )
    ]
    turns += [
        SpokenTurn(
            row=CallTranscript(
                call_execution=call,
                speaker_role=ACTOR,
                content=f"line {index}",
                start_time_ms=index,
            ),
            speaker_name=f"Actor {index}",
            actor_id=f"actor_{index}",
        )
        for index in range(1, actor_turns + 1)
    ]
    # An actor turn that names nobody keeps no speaker row.
    turns.append(
        SpokenTurn(
            row=CallTranscript(call_execution=call, speaker_role=ACTOR, content="?")
        )
    )
    return turns


def test_storing_a_transcript_keeps_who_spoke_only_the_actor_turns_that_name_one(
    call_execution,
):
    store_transcript(_turns(call_execution, 3))

    assert CallTranscript.objects.filter(call_execution=call_execution).count() == 5
    assert sorted(
        CallTranscriptActor.objects.filter(
            transcript__call_execution=call_execution
        ).values_list("speaker_name", "actor_id")
    ) == [("Actor 1", "actor_1"), ("Actor 2", "actor_2"), ("Actor 3", "actor_3")]


@pytest.mark.parametrize("actor_turns", [1, 12])
def test_a_multi_turn_read_with_its_speakers_is_one_query(call_execution, actor_turns):
    store_transcript(_turns(call_execution, actor_turns))

    with CaptureQueriesContext(connection) as queries:
        served = CallTranscriptSerializer(
            with_speakers(call_execution.transcripts.all()).order_by(
                "start_time_ms", "content"
            ),
            many=True,
        ).data

    assert len(queries) == 1
    assert len(served) == actor_turns + 2
    spoken = [
        (one["speaker_name"], one["actor_id"])
        for one in served
        if one["speaker_role"] == ACTOR
    ]
    assert ("Actor 1", "actor_1") in spoken and ("", None) in spoken
    assert all(
        (one["speaker_name"], one["actor_id"]) == ("", None)
        for one in served
        if one["speaker_role"] == "user"
    )


def test_the_call_detail_transcript_reads_each_speaker_once(call_execution):
    from simulate.serializers.test_execution import CallExecutionDetailSerializer

    store_transcript(_turns(call_execution, 6))

    with CaptureQueriesContext(connection) as queries:
        rows = CallExecutionDetailSerializer().get_transcript(call_execution)

    assert (
        len([one for one in queries if "simulate_call_transcript" in one["sql"]]) == 1
    )
    assert {one["actor_id"] for one in rows if one["speaker_role"] == ACTOR} >= {
        "actor_1",
        "actor_6",
    }
