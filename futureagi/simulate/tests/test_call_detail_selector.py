"""A call's detail arrives ready to show: actor turns, world actors' actions and the trial's World state are worked
out by the server, never guessed by the page."""

from types import SimpleNamespace

import pytest

from simulate.selectors.call_detail import (
    tagged_function_calls,
    transcript_turns,
    trial_state,
)
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.utils.stored_transcript_roles import call_actor_label

SAM = {"id": "partner", "name": "Sam", "role": "partner", "channel": "chat"}


def _call(state):
    return SimpleNamespace(id="c1", call_metadata={TRIAL_STATE_KEY: state})


def test_a_chat_row_an_actor_wrote_is_that_actor_s_turn_by_id_without_the_label_the_agent_saw():
    label = f"{call_actor_label('Sam')}: "
    rows = [
        {
            "role": "user",
            "content": [{"role": "user", "content": "Hi, order 1043 please"}],
        },
        {
            "role": "user",
            "content": [
                {
                    "role": "user",
                    "actor": "partner",
                    "content": label + "We both agreed.",
                }
            ],
        },
        {"role": "user", "content": [{"role": "task", "content": "Start the refund"}]},
    ]

    person, actor, task = transcript_turns(rows, [SAM], chat=True)

    assert person["speaker_role"] == "user"
    assert (actor["speaker_role"], actor["actor_id"], actor["speaker_name"]) == (
        "actor",
        "partner",
        "Sam",
    )
    assert (actor["actor_role"], actor["channel"], actor["content"]) == (
        "partner",
        "chat",
        "We both agreed.",
    )
    assert task["speaker_role"] == "task"


def test_a_person_who_quotes_the_label_stays_the_person():
    quoted = {
        "role": "user",
        "content": [{"role": "user", "content": f"{call_actor_label('Sam')}: hello"}],
    }

    [turn] = transcript_turns([quoted], [SAM], chat=True)

    assert (turn["speaker_role"], turn["content"]) == ("user", quoted["content"])


@pytest.mark.parametrize(
    "item, speaker",
    [
        ({"content": f"{call_actor_label('alex_vance')}: Approved."}, "alex_vance"),
        (
            {
                "name": "Alex Vance",
                "content": f"{call_actor_label('Alex Vance')}: Approved.",
            },
            "Alex Vance",
        ),
    ],
    ids=["by its id", "by the name its turn carries"],
)
def test_an_actor_the_cast_does_not_list_still_speaks_as_itself_without_the_label(
    item, speaker
):
    row = {"role": "user", "content": [{"role": "user", "actor": "alex_vance", **item}]}

    [turn] = transcript_turns([row], [SAM], chat=True)

    assert (
        turn["speaker_role"],
        turn["actor_id"],
        turn["speaker_name"],
        turn["content"],
    ) == (
        "actor",
        "alex_vance",
        speaker,
        "Approved.",
    )


def test_a_voice_actor_turn_carries_its_role_and_channel():
    [turn] = transcript_turns(
        [{"speaker_role": "actor", "actor_id": "partner", "content": "hi"}],
        [SAM],
        chat=False,
    )

    assert (turn["actor_role"], turn["channel"]) == ("partner", "chat")


def test_each_recorded_call_says_whose_it_is_and_an_old_action_reads_refused_or_not_recorded():
    agent, acted, old, unlogged = tagged_function_calls(
        [
            {"name": "get_order", "actor_id": "agent"},
            {
                "name": "refunds.change",
                "actor_id": "approver",
                "actor_name": "Pat",
                "outcome": "committed",
            },
            {"name": "refunds.change", "actor_id": "approver", "refused": True},
            {"name": "refunds.change", "actor_id": "approver"},
        ]
    )

    assert (
        agent["kind"] == "agent_call"
        and "outcome" not in agent
        and "actor_name" not in agent
    )
    assert (acted["kind"], acted["outcome"], acted["actor_name"]) == (
        "world_actor_action",
        "committed",
        "Pat",
    )
    assert (old["kind"], old["outcome"], old["actor_name"]) == (
        "world_actor_action",
        "refused",
        "approver",
    )
    assert unlogged["outcome"] == "not_recorded"


def test_world_state_splits_pre_run_messages_and_names_a_fallback_the_world_did_not_commit():
    shown = trial_state(
        _call(
            {
                "decisions": [
                    {
                        "actor": "partner",
                        "name": "Sam",
                        "play": "history",
                        "arguments": {"text": "hi"},
                    },
                    {
                        "actor": "approver",
                        "play": "timeout",
                        "outcome": "fallback_action",
                        "action_outcome": "refused",
                    },
                    {
                        "actor": "approver",
                        "play": "timeout",
                        "outcome": "fallback_action",
                        "action_outcome": "committed",
                    },
                ]
            }
        )
    )

    assert [one["play"] for one in shown["pre_run"]] == ["history"]
    assert [one["outcome"] for one in shown["decisions"]] == [
        "fallback_not_committed",
        "fallback_action",
    ]


def test_world_state_sums_actors_model_use_per_actor_and_model_then_in_all():
    shown = trial_state(
        _call(
            {
                "state_changes": [{"who": "partner", "name": "Sam"}],
                "usage": [
                    {
                        "actor": "partner",
                        "model": "m",
                        "tokens_in": 10,
                        "tokens_out": 5,
                        "cost_usd": 0.01,
                    },
                    {
                        "actor": "partner",
                        "model": "m",
                        "tokens_in": 1,
                        "tokens_out": 1,
                        "cost_usd": 0.02,
                    },
                    {
                        "actor": "approver",
                        "model": "m",
                        "tokens_in": 4,
                        "tokens_out": 0,
                        "cost_usd": 0.0,
                    },
                ],
            }
        )
    )

    assert shown["usage_summary"] == [
        {
            "actor": "partner",
            "name": "Sam",
            "model": "m",
            "calls": 2,
            "tokens": 17,
            "cost_usd": 0.03,
        },
        {
            "actor": "approver",
            "name": "approver",
            "model": "m",
            "calls": 1,
            "tokens": 4,
            "cost_usd": 0.0,
        },
    ]
    assert shown["usage_total"] == {"calls": 3, "tokens": 21, "cost_usd": 0.03}
    assert "usage_total" not in trial_state(_call({}))


def test_world_state_says_first_when_a_trial_did_not_start_from_the_seeded_records():
    shown = trial_state(
        _call(
            {
                "start": {"state": "differs", "tables": ["orders"]},
                "notices": [{"code": "fault_not_triggered"}],
            }
        )
    )

    assert [one["code"] for one in shown["notices"]] == [
        "start_differs",
        "fault_not_triggered",
    ]
    assert "orders" in shown["notices"][0]["message"]


def test_commits_with_no_record_change_say_the_agent_keeps_its_records_outside_the_test_world():
    committed = {
        "who": "agent",
        "name": "the agent",
        "committed": 1,
        "committed_actions": ["issue_refund"],
    }

    outside = trial_state(
        _call({"state_changes": [committed], "records": {"changes": []}})
    )
    changed = trial_state(
        _call(
            {
                "state_changes": [committed],
                "records": {"changes": [{"table": "refunds"}]},
            }
        )
    )
    nothing = trial_state(
        _call(
            {
                "state_changes": [{**committed, "committed": 0}],
                "records": {"changes": []},
            }
        )
    )

    assert [one["code"] for one in outside["notices"]] == ["changed_outside_world"]
    assert changed["notices"] == [] and nothing["notices"] == []


def test_the_agent_s_participant_heading_reads_as_a_heading_and_an_actor_keeps_its_own_name():
    state = trial_state(
        _call(
            {
                "state_changes": [
                    {"who": "agent", "name": "the agent", "committed": 0},
                    {"who": "sam", "name": "Sam Taylor", "committed": 0},
                ]
            }
        )
    )

    assert [one["name"] for one in state["state_changes"]] == [
        "The agent",
        "Sam Taylor",
    ]


def test_a_world_state_that_is_not_an_object_reads_as_not_recorded():
    assert trial_state(_call(["not", "an", "object"])) is None
    assert trial_state(SimpleNamespace(id="c1", call_metadata=None)) is None


def test_an_actor_that_acted_in_the_world_without_a_line_is_served_as_acted_not_dropped():
    from simulate.selectors.call_detail import call_actors

    metadata = {
        "harness_call_actors": {
            "resolved": [
                {"id": "coworker", "name": "Jordan"},
                {"id": "manager", "name": "Alex"},
            ],
            "summary": [
                {"actor": "coworker", "triggered": True, "delivered": 0, "acted": 1},
                {"actor": "manager", "triggered": True, "delivered": 0, "acted": 0},
            ],
        }
    }

    jordan, alex = call_actors(metadata)

    assert (jordan["delivery"], jordan["delivery_label"]) == (
        "acted",
        "Acted in the world, said no line",
    )
    assert alex["delivery"] == "dropped"


def test_a_trial_on_a_seal_built_before_the_world_door_says_its_world_actions_are_not_known():
    pre_door = trial_state(_call({"recorded": False, "why": "sealed_without_door"}))
    in_process = trial_state(_call({"recorded": False, "why": "in_process_tools"}))

    assert pre_door["recorded"] is False
    assert pre_door["why_label"].startswith(
        "This environment was built before world actions were recorded"
    )
    assert "Rebuild" in pre_door["why_label"]
    assert "attempted" not in pre_door["why_label"]
    assert in_process["why_label"].startswith(
        "World state is not recorded for this agent"
    )


# A trial stored before the platform read pre-door seals: the agent's counts are zeros while its 8 calls were recorded.
OLD_PRE_DOOR_RECORD = {
    "start": {"state": "seeded", "tables": [], "against": "sealed_seed"},
    "state_changes": [
        {
            "who": "agent",
            "name": "the agent",
            "read": 0,
            "faulted": 0,
            "refused": 0,
            "attempted": 0,
            "committed": 0,
            "duplicate": 0,
            "no_change": True,
            "read_actions": [],
            "committed_actions": [],
        }
    ],
    "records": {"changes": [], "skipped": [], "truncated": 0},
}


def test_a_stored_trial_whose_agent_calls_never_reached_the_world_reads_not_known_not_zero():
    calls = [{"name": "find_items", "arguments": {"query": "kayak"}}] * 8

    shown = trial_state(_call(OLD_PRE_DOOR_RECORD), calls=calls)

    assert (shown["recorded"], shown["why"]) == (False, "calls_not_seen")
    assert "not known" in shown["why_label"] and "attempted" not in shown["why_label"]


def test_a_stored_trial_with_no_tool_calls_keeps_its_counts():
    shown = trial_state(_call(OLD_PRE_DOOR_RECORD), calls=[])

    assert "recorded" not in shown
    assert shown["state_changes"][0]["attempted"] == 0


def test_a_new_door_trial_whose_agent_called_only_its_own_tools_keeps_no_change():
    # On an environment with a door, a tool that runs inside the agent (prepare_cancellation) never reaches the world.
    from simulate.services.hosted_harness_ingestion import _world_change_fields

    stamped = {**OLD_PRE_DOOR_RECORD, **_world_change_fields({"door": True})}
    calls = [{"name": "prepare_cancellation", "arguments": {}}]

    shown = trial_state(_call(stamped), calls=calls)

    assert "recorded" not in shown
    assert shown["state_changes"][0]["no_change"] is True


def test_a_new_trial_on_a_seal_without_a_door_reads_built_before_world_actions_were_recorded():
    calls = [{"name": "find_items", "arguments": {"query": "kayak"}}] * 8

    shown = trial_state(
        _call({"recorded": False, "why": "sealed_without_door"}), calls=calls
    )

    assert (shown["recorded"], shown["why"]) == (False, "sealed_without_door")
    assert shown["why_label"].startswith(
        "This environment was built before world actions were recorded"
    )
