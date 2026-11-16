"""A call's detail arrives ready to show: actor turns, world actors' actions and the trial's World state are worked
out by the server, never guessed by the page."""

from types import SimpleNamespace

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

    assert "speaker_role" not in person
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


def test_a_person_who_quotes_the_label_stays_the_person_and_an_unknown_actor_is_left_as_stored():
    quoted = {
        "role": "user",
        "content": [{"role": "user", "content": f"{call_actor_label('Sam')}: hello"}],
    }
    stranger = {
        "role": "user",
        "content": [{"role": "user", "actor": "nobody", "content": "hi"}],
    }

    assert transcript_turns([quoted, stranger], [SAM], chat=True) == [quoted, stranger]


def test_a_voice_actor_turn_carries_its_role_and_channel():
    [turn] = transcript_turns(
        [{"speaker_role": "actor", "actor_id": "partner", "content": "hi"}],
        [SAM],
        chat=False,
    )

    assert (turn["actor_role"], turn["channel"]) == ("partner", "chat")


def test_each_recorded_call_says_whose_it_is_and_an_old_refused_action_reads_refused():
    agent, acted, old = tagged_function_calls(
        [
            {"name": "get_order", "actor_id": "agent"},
            {"name": "refunds.change", "actor_id": "approver", "outcome": "committed"},
            {"name": "refunds.change", "actor_id": "approver", "refused": True},
        ]
    )

    assert agent["kind"] == "agent_call" and "outcome" not in agent
    assert (acted["kind"], acted["outcome"]) == ("world_actor_action", "committed")
    assert (old["kind"], old["outcome"]) == ("world_actor_action", "refused")


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


def test_a_world_state_that_is_not_an_object_reads_as_not_recorded():
    assert trial_state(_call(["not", "an", "object"])) is None
    assert trial_state(SimpleNamespace(id="c1", call_metadata=None)) is None
